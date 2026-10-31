"""Repository reading models and translations bound to exact source content."""
import hashlib
import json
import re

from .db import now

FIELDS = ('description', 'readme', 'readme_summary')
LANGUAGES = ('zh-CN', 'en')


def sources(project, snapshot):
    metadata = json.loads(project['metadata'])
    evidence = json.loads(snapshot['evidence']) if snapshot else {}
    description = metadata.get('description') or ''
    readme = evidence.get('readme_text') or ''
    # Do not treat API objects or generated JSON as natural-language source text.
    if not isinstance(description, str):
        description = ''
    if not isinstance(readme, str):
        readme = ''
    urls = evidence.get('sources') or {}
    result = {}
    for field, text in (('description',description),('readme',readme),('readme_summary',readme)):
        result[field] = {'text':text, 'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'truncated':field!='description' and bool(evidence.get('readme_text_truncated',len(readme)>=40000)),
            'url':project['url'] if field=='description' else urls.get('readme',project['url']),
            'captured_at':project['refreshed_at'] if field=='description' else snapshot['fetched_at'] if snapshot else None}
    return result


def dossier(store, project, snapshot=None):
    if snapshot is None:
        snapshot = store.one('SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1',(project['id'],))
    original = sources(project,snapshot)
    rows = store.all('SELECT * FROM dossier_translations WHERE project_id=? ORDER BY id DESC',(project['id'],))
    translations = {}
    for field in FIELDS:
        values = {}
        for language in LANGUAGES:
            row = next((r for r in rows if r['field']==field and r['language']==language),None)
            state = 'missing'
            if row:
                state = 'current' if original[field]['text'] and row['source_sha256']==original[field]['sha256'] else 'stale'
            values[language] = {'status':state, 'translation':row if state=='current' else None,
                                'previous_updated_at':row['created_at'] if row else None}
        translations[field] = {'source':original[field], 'languages':values}
    return {'fields':translations, 'languages':list(LANGUAGES)}


def save_translation(store, project_id, data, actor):
    if not isinstance(data,dict) or set(data) != {'field','language','source_sha256','content','method'}:
        raise ValueError('Translation requires field, language, source_sha256, content and method')
    if data['field'] not in FIELDS or data['language'] not in LANGUAGES or data['method'] not in ('human','assistant','provider'):
        raise ValueError('Unsupported translation field, language or method')
    if not isinstance(data['source_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',data['source_sha256']):
        raise ValueError('Translation must identify the exact source digest')
    if not isinstance(data['content'],str) or not data['content'].strip() or len(data['content'])>100000 or len(data['content'].encode('utf-8'))>400000:
        raise ValueError('Translation content must be nonempty text within 100000 characters and 400000 bytes')
    if not isinstance(actor,str) or not actor.strip():
        raise ValueError('An accountable translator is required')
    with store.transaction():
        project = store.one('SELECT * FROM projects WHERE id=?',(project_id,))
        if not project:
            raise ValueError('Project not found')
        snapshot = store.one('SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1',(project_id,))
        source = sources(project,snapshot)[data['field']]
        if not source['text'] or source['sha256'] != data['source_sha256']:
            raise ValueError('Translation source changed or is missing; reload the repository dossier')
        latest = store.one('SELECT * FROM dossier_translations WHERE project_id=? AND field=? AND language=? ORDER BY id DESC LIMIT 1',
                           (project_id,data['field'],data['language']))
        if latest and all(latest[key]==data[key] for key in ('source_sha256','content','method')):
            return {'id':latest['id'],'project_id':project_id,'duplicate':True}
        cur = store.conn.execute('INSERT INTO dossier_translations(project_id,field,language,source_sha256,content,method,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (project_id,data['field'],data['language'],data['source_sha256'],data['content'],data['method'],actor,now()))
        store.event('project',project_id,'dossier-translated',actor,{'translation_id':cur.lastrowid,'field':data['field'],
            'language':data['language'],'source_sha256':data['source_sha256'],'method':data['method']})
        return {'id':cur.lastrowid,'project_id':project_id,'duplicate':False}


def export_translation_work(store, field='description', language='zh-CN', limit=200):
    if field not in FIELDS or language not in LANGUAGES or type(limit) is not int or not 1<=limit<=1000:
        raise ValueError('Invalid translation work selection')
    items=[]
    for project in store.all('SELECT * FROM projects ORDER BY id'):
        data=dossier(store,project)['fields'][field]
        if data['source']['text'] and data['languages'][language]['status']!='current':
            items.append({'project_id':project['id'],'full_name':project['full_name'],'field':field,'language':language,
                'source_sha256':data['source']['sha256'],'source_text':data['source']['text'],
                'source_url':data['source']['url'],'content':'','method':'human'})
            if len(items)>=limit:
                break
    return {'items':items,'count':len(items),'field':field,'language':language}


def import_translations(store, document, actor):
    items=document.get('items') if isinstance(document,dict) else None
    if not isinstance(items,list) or not 1<=len(items)<=1000:
        raise ValueError('Translation import requires 1..1000 items')
    results=[]
    # A stale source rejects the whole batch; no partial, unreviewable application.
    with store.transaction():
        for item in items:
            if not isinstance(item,dict) or type(item.get('project_id')) is not int:
                raise ValueError('Translation item requires a numeric project_id')
            data={key:item[key] for key in ('field','language','source_sha256','content','method')}
            results.append(save_translation(store,item['project_id'],data,actor))
    return {'items':results,'count':len(results)}
