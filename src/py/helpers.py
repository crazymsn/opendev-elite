import random
import string
import time
import os

def random_str(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def sleep_random(min_sec=0.1, max_sec=0.5):
    time.sleep(random.uniform(min_sec, max_sec))

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def write_append(filepath, content):
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(content + '\n')

def safe_cast(val, to_type, default=None):
    try:
        return to_type(val)
    except (TypeError, ValueError, OverflowError):
        return default

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def merge_dicts(*dict_args):
    result = {}
    for d in dict_args:
        if isinstance(d, dict):
            result.update(d)
    return result

def timestamp_str():
    return time.strftime('%Y%m%d_%H%M%S', time.localtime())
