"""Bounded, deterministic evidence pointers, never a quality verdict."""
from pathlib import PurePosixPath
import re

STRATEGY = 'repository-evidence-v2'
MAX_BYTES = 80000
SOURCE_EXTENSIONS = frozenset(('.py','.ts','.tsx','.js','.jsx','.mjs','.cjs','.go','.rs','.c','.h','.cc',
    '.cpp','.cxx','.hpp','.cs','.java','.kt','.kts','.scala','.rb','.php','.swift','.m','.mm','.jl',
    '.ex','.exs','.clj','.cljs','.sh','.bash','.lua','.zig','.r','.vue','.svelte','.dart','.elm','.hs'))
DEPENDENCY_NAMES = frozenset(('pyproject.toml','package.json','cargo.toml','go.mod','project.toml',
    'pom.xml','build.gradle','build.gradle.kts','gemfile','composer.json','mix.exs','pubspec.yaml',
    'cmakelists.txt','requirements.txt','setup.py','build.zig','package.swift'))
IGNORED = frozenset(('vendor','vendors','third_party','third-party','node_modules','.git','.venv','venv',
    '__pycache__','dist','build','target','coverage','generated','__generated__','.next'))
SUPPORT = frozenset(('bench','benchmark','benchmarks','example','examples','fixture','fixtures','docs',
    'doc','docsite','scripts','experiments','.github','.claude','.agentic'))
TEST_DIRS = frozenset(('test','tests','__tests__','testing','spec','specs'))
ENTRY_NAMES = frozenset(('main','__main__','app','server','cli','index','lib','application'))


def _test(path):
    p=PurePosixPath(path.lower())
    return bool(set(p.parts[:-1]) & TEST_DIRS or p.name=='conftest.py' or p.stem=='runtests'
                or re.search(r'(^Test[A-Z]|(?:Test|Tests|Spec)$)',PurePosixPath(path).stem)
                or re.search(r'(^test[_.-]|[_.-](test|spec)([_.-]|$))',p.stem))


def _generated(path):
    p=PurePosixPath(path.lower())
    return bool(set(p.parts[:-1]) & IGNORED or p.name.endswith(('.min.js','.min.css','.d.ts'))
                or re.search(r'(_pb2?\.|\.pb\.|\.g\.|\.generated\.)',p.name))


def _rank(item):
    path=PurePosixPath(item['path'].lower());dirs=set(path.parts[:-1])
    # Segment matches avoid accidentally excluding e.g. latest.py or contest/.
    supporting=bool(dirs & SUPPORT)
    entry=path.stem in ENTRY_NAMES
    source=bool(dirs & {'src','lib','internal','server','core','app','apps','pkg','packages','crates'})
    helper=path.stem in ('__init__','_version','version','types','constants','config','settings')
    return (supporting,not source,helper,not entry,len(path.parts),item['path'].casefold(),item['path'])


def _diverse(rows,limit):
    remaining=sorted(rows,key=_rank);chosen=[];parents=set()
    while remaining and len(chosen)<limit:
        # Keep implementation ahead of support files, then prefer a new directory.
        best=min(remaining,key=lambda item:(_rank(item)[0],PurePosixPath(item['path']).parent.as_posix() in parents,*_rank(item)[1:]))
        remaining.remove(best);chosen.append(best);parents.add(PurePosixPath(best['path']).parent.as_posix())
    return chosen


def select_samples(tree):
    """At most three implementation, one test, one manifest and one CI file."""
    rows=[];seen=set();omitted={'generated_or_vendor':0,'oversized':0,'empty':0,'unsupported':0,'invalid':0}
    for item in tree:
        if not isinstance(item,dict) or item.get('type')!='blob':
            continue
        path=item.get('path')
        if (not isinstance(path,str) or not path or path.startswith('/') or '\\' in path
                or any(part in ('.','..','') for part in path.split('/')) or path in seen):
            omitted['invalid']+=1;continue
        seen.add(path)
        if _generated(path):omitted['generated_or_vendor']+=1;continue
        size=item.get('size')
        if type(size) is not int or not 0<=size<=MAX_BYTES:omitted['oversized']+=1;continue
        if size==0:omitted['empty']+=1;continue
        rows.append(item)
    runtime=[];tests=[];manifests=[];ci=[]
    for item in rows:
        path=PurePosixPath(item['path']);lower=path.name.lower()
        if item['path'].startswith('.github/workflows/') and path.suffix.lower() in ('.yml','.yaml'):
            ci.append(item)
        elif lower in DEPENDENCY_NAMES or path.suffix.lower() in ('.csproj','.fsproj'):
            manifests.append(item)
        elif path.suffix.lower() in SOURCE_EXTENSIONS:
            (tests if _test(item['path']) else runtime).append(item)
        else:omitted['unsupported']+=1
    selected=[]
    for role,candidates,limit in (('implementation',runtime,3),('test',tests,1),('dependency_manifest',manifests,1),('ci_configuration',ci,1)):
        if role=='dependency_manifest':
            choices=sorted(candidates,key=lambda x:(len(PurePosixPath(x['path']).parts),*_rank(x)))[:limit]
        elif role=='ci_configuration':
            choices=sorted(candidates,key=lambda x:(not bool(re.search(r'(test|ci|check)',PurePosixPath(x['path']).stem,re.I)),x['path']))[:limit]
        elif role=='test':
            def test_rank(item):
                stem=PurePosixPath(item['path']).stem
                named=bool(re.search(r'(^test[_.-]|[_.-](test|spec)$|^Test[A-Z]|(?:Test|Tests|Spec)$)',stem))
                return (_rank(item)[0],not named,stem!='runtests',*_rank(item)[1:])
            choices=sorted(candidates,key=test_rank)[:limit]
        else:choices=_diverse(candidates,limit)
        for item in choices:
            selected.append({'path':item['path'],'role':role,'size':item['size'],'blob_sha':item.get('sha')})
    return {'strategy':STRATEGY,'files':selected,'max_files':6,'max_file_bytes':MAX_BYTES,
            'candidate_counts':{'implementation':len(runtime),'test':len(tests),'dependency_manifest':len(manifests),'ci_configuration':len(ci)},
            'omitted_counts':omitted,'warning':'Deterministic path-based samples; reviewers must inspect the actual core workflow and expand evidence where needed. Presence is not test success, code quality, maintenance or product value.'}
