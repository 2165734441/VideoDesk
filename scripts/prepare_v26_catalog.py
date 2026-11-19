"""Maintainer-only: pin official assets; never used to trust arbitrary runtime URLs."""
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def read(url):
    with urlopen(Request(url, headers={'User-Agent': 'VideoDesk-build'}), timeout=120) as response:
        return response.read()


def main():
    target = ROOT/'integrations/translation-catalog.json'
    entries = {}
    for key, repo, files in (
        ('english-asr', 'Systran/faster-whisper-small.en', ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt', 'README.md']),
        ('english-chinese', 'Helsinki-NLP/opus-mt-en-zh', ['config.json', 'generation_config.json', 'pytorch_model.bin', 'source.spm', 'target.spm', 'tokenizer_config.json', 'vocab.json', 'README.md']),
        ('background', 'adefossez/HTDemucs', ['htdemucs.yaml', '955717e8.safetensors', 'README.md']),
    ):
        metadata = json.loads(read('https://huggingface.co/api/models/'+repo+'?blobs=true'))
        entry = {'repo': repo, 'revision': metadata['sha'], 'files': []}
        for name in files:
            item = next(s for s in metadata['siblings'] if s['rfilename'] == name)
            url = f'https://huggingface.co/{repo}/resolve/{metadata["sha"]}/{name}'
            sha = item.get('lfs', {}).get('sha256') or hashlib.sha256(read(url)).hexdigest()
            entry['files'].append({'name': name, 'url': url, 'sha256': sha, 'bytes': item['size']})
        entries[key] = entry
    cache = ROOT/'.local/v26-downloads'; cache.mkdir(exist_ok=True)
    urls = {'python': 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip',
            'pip': 'https://bootstrap.pypa.io/pip/pip.pyz',
            'chinese-voice': 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2'}
    for key, url in urls.items():
        path = cache/url.rsplit('/', 1)[-1]
        if not path.is_file():
            print('Download official asset:', key, flush=True)
            with urlopen(Request(url, headers={'User-Agent': 'VideoDesk-build'}), timeout=120) as response, path.open('wb') as dest:
                while part := response.read(1024*1024): dest.write(part)
        entries[key] = {'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    target.write_text(json.dumps(entries, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Pinned catalog:', target, flush=True)


if __name__ == '__main__': main()
