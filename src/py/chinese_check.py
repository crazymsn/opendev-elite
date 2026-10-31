import os
import pandas as pd
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import queue

_lock = threading.Lock()
_result_queue = queue.Queue()

def _compose_prompt(user_info):
    parts = []
    keys = ['GitHub_ID', 'Name', 'Org', 'Bio', 'Location', 'Email']
    for k in keys:
        v = user_info.get(k, '')
        parts.append(f"{k}={v}")
    return "根据以下信息判断此人是否可能是华人（只输出“是（理由）”或“否（理由）”，理由15字内）：" + "; ".join(parts)

def _api_request(payload, api_key):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        return content, usage
    except Exception as e:
        return f"API调用失败({str(e)})", {}

def _worker_task(index, user_info, api_key):
    prompt = _compose_prompt(user_info)
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "只输出“是（理由）”或“否（理由）”，理由15字内。"},
            {"role": "user", "content": prompt}
        ]
    }
    result, usage = _api_request(payload, api_key)
    return index, result, usage

def process(input_file, output_file, api_keys, save_interval=20, max_workers=None):
    if not os.path.isfile(input_file):
        return

    df = pd.read_csv(input_file, sep='\t', encoding='utf-8-sig')
    if 'Chinese_Check' not in df.columns:
        df['Chinese_Check'] = ""
    if 'Chinese_Check_Reason' not in df.columns:
        df['Chinese_Check_Reason'] = ""

    df['Chinese_Check'] = df['Chinese_Check'].astype(str)
    df['Chinese_Check_Reason'] = df['Chinese_Check_Reason'].astype(str)

    total = len(df)
    futures = []
    updated_count = 0
    keys_len = len(api_keys)
    max_workers = max_workers or keys_len

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, row in df.iterrows():
            chk = row['Chinese_Check'].strip()
            reason = row['Chinese_Check_Reason'].strip()
            gh_id = str(row.get('GitHub_ID', '')).strip()
            if chk or reason or gh_id.startswith("index"):
                continue
            key = api_keys[idx % keys_len]
            futures.append(executor.submit(_worker_task, idx, row.to_dict(), key))

        for future in as_completed(futures):
            idx, result, usage = future.result()
            if result.startswith("是") or result.startswith("否"):
                check = result[0]
                reason = result[1:].strip("（）:： \n")
            else:
                check, reason = "NA", result

            with _lock:
                df.at[idx, 'Chinese_Check'] = check
                df.at[idx, 'Chinese_Check_Reason'] = reason
                updated_count += 1

                if updated_count >= save_interval:
                    try:
                        df.to_csv(input_file, sep='\t', index=False, encoding='utf-8-sig')
                        chinese_df = df[df['Chinese_Check'].str.startswith("是", na=False)]
                        chinese_df.to_csv(output_file, sep='\t', index=False, encoding='utf-8-sig')
                    except Exception:
                        pass
                    updated_count = 0
            time.sleep(0.3)

    try:
        df.to_csv(input_file, sep='\t', index=False, encoding='utf-8-sig')
        chinese_df = df[df['Chinese_Check'].str.startswith("是", na=False)]
        if not chinese_df.empty:
            chinese_df.to_csv(output_file, sep='\t', index=False, encoding='utf-8-sig')
    except Exception:
        pass
