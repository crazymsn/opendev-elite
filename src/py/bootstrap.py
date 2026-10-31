"""Compatibility bootstrap using one Pipeline; no ethnicity inference is loaded."""
import os


def main(args):
    from src.py.fetcher import fetch_github_repos
    return fetch_github_repos(args.get('token', ''), args.get('base_dir', os.getcwd()),
                              args.get('topic', 'topic:artificial-intelligence'),
                              args.get('date_start'), args.get('date_end'),
                              max_per_day=args.get('max_per_day', 1000),
                              batch_size=args.get('batch_size', 10), client=args.get('client'))
