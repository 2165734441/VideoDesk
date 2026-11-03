"""Migration and validation of independently persisted workflow preferences."""
from copy import deepcopy

KINDS = ('board', 'rules', 'subtitles')


def defaults(kind):
    return {'outputs': {'frames': kind=='rules', 'audio': False, 'puzzles': kind!='rules',
                        'transcript': kind=='board', 'clips': False, 'ai': kind=='board', 'zip': False},
            'dedup': {'enabled': False, 'mode': 'exact', 'threshold': 4},
            'segments': {'enabled': False, 'seconds': 10, 'organize': True},
            'ai_package': {'enabled': kind=='board', 'combined': False, 'screen_text': True,
                           'ocr_threads': 2, 'scan_seconds': 1, 'batch_files': 10},
            'performance': 'auto', 'fields': {}, 'online_episodes': {'mode':'separate','items':[]}}


def migrate(saved):
    workflows = {}
    for kind in KINDS:
        base = defaults(kind)
        previous = saved.get('workflows', {}).get(kind)
        if previous is None and kind=='board': previous = saved.get('settings', {})
        for key, value in (previous or {}).items():
            if key in base:
                base[key] = {**base[key], **deepcopy(value)} if isinstance(base[key], dict) and isinstance(value, dict) else deepcopy(value)
        if kind=='rules':
            base['outputs'].update(puzzles=False, transcript=False, ai=False)
            base['ai_package'].update(enabled=False, combined=False)
        elif kind=='board':
            base['outputs'].update(puzzles=True, transcript=True, ai=True)
            base['ai_package']['enabled'] = True
        workflows[kind] = base
    return workflows
