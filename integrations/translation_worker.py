"""Private optional AI process. JSON-lines requests; no credentials in arguments."""
import gc
import json
import os
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('OMP_NUM_THREADS', '2')

_dll_handles = []
if sys.platform == 'win32':
    # The optional GPU PyTorch wheel supplies CUDA 12.8 / cuDNN 9 DLLs for CT2.
    lib = Path(sys.executable).parent/'Lib/site-packages/torch/lib'
    if lib.is_dir():
        _dll_handles.append(os.add_dll_directory(str(lib)))
        os.environ['PATH'] = str(lib)+os.pathsep+os.environ.get('PATH', '')


def emit(value): print(json.dumps(value, ensure_ascii=False), flush=True)


def write_wav(path, samples, rate):
    import numpy as np
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes((np.clip(samples, -1, 1)*32767).astype('<i2').tobytes())


def convert(folder):
    from ctranslate2.converters import TransformersConverter
    folder = Path(folder); dest = folder/'ct2'
    TransformersConverter(str(folder), copy_files=[]).convert(str(dest), quantization='int8', force=True)


class Engine:
    def __init__(self):
        self.model = None; self.kind = None; self.device = 'cpu'; self.verified = set()

    def reset(self, kind):
        if self.kind == kind: return
        self.model = None; self.kind = kind; gc.collect()
        torch = sys.modules.get('torch')
        if torch and torch.cuda.is_available(): torch.cuda.empty_cache()

    def call(self, req):
        kind = req['stage']; self.reset(kind)
        folder = Path(req['models']); requested = req.get('device', 'auto')
        key = {'asr': 'english-asr', 'translate': 'english-chinese', 'tts': 'chinese-voice', 'separate': 'background'}[kind]
        if key not in self.verified:
            from integrations.translation_components import verify
            verify(folder.parent, key); self.verified.add(key)
        device = 'cpu'
        if requested != 'cpu':
            try:
                if kind in ('asr', 'translate'):
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0: device = 'cuda'
                elif kind == 'separate':
                    import torch
                    if torch.cuda.is_available(): device = 'cuda'
            except Exception:
                emit({'progress': '显卡运行库不可用，当前阶段使用 CPU', 'fallback': kind})
        try: result = self.execute(req, folder, device)
        except Exception:
            if device == 'cpu': raise
            self.model = None; gc.collect()
            emit({'progress': '显卡推理未通过，当前阶段回退 CPU', 'fallback': kind})
            device = 'cpu'; result = self.execute(req, folder, device)
        return {'data': result, 'device': device}

    def execute(self, req, folder, device):
        kind = req['stage']
        if kind == 'asr':
            from faster_whisper import WhisperModel
            if self.model is None:
                self.model = WhisperModel(str(folder/'english-asr'), device=device,
                    compute_type='int8_float16' if device == 'cuda' else 'int8', cpu_threads=req.get('threads', 2), local_files_only=True)
            segments, _ = self.model.transcribe(req['audio'], language='en', vad_filter=True, word_timestamps=True, beam_size=5)
            result = []
            for item in segments:
                result.append({'start': round(item.start, 3), 'end': round(item.end, 3), 'en': item.text.strip(), 'zh': ''})
                emit({'progress': f'已识别 {len(result)} 句', 'seconds': item.end})
            return result
        if kind == 'translate':
            import ctranslate2
            import sentencepiece
            if self.model is None:
                self.model = (ctranslate2.Translator(str(folder/'english-chinese/ct2'), device=device,
                              compute_type='int8_float16' if device == 'cuda' else 'int8', inter_threads=1, intra_threads=req.get('threads', 2)),
                              sentencepiece.SentencePieceProcessor(model_proto=(folder/'english-chinese/source.spm').read_bytes()),
                              sentencepiece.SentencePieceProcessor(model_proto=(folder/'english-chinese/target.spm').read_bytes()))
            translator, source_tokenizer, target_tokenizer = self.model
            rows = req['segments']
            for offset in range(0, len(rows), 8):
                batch = rows[offset:offset+8]
                # Loading SPM bytes avoids its narrow Windows path API on Chinese user folders.
                tokens = [source_tokenizer.encode(row['en'], out_type=str)+['</s>'] for row in batch]
                if any(len(t) > 512 for t in tokens): raise ValueError('英文句子过长，请拆分字幕后重试')
                translated = translator.translate_batch(tokens, beam_size=4, max_decoding_length=512)
                for row, output in zip(batch, translated):
                    row['zh'] = target_tokenizer.decode([t for t in output.hypotheses[0] if t not in ('</s>', '<s>', '<pad>')]).strip()
                emit({'progress': f'已翻译 {min(offset+8, len(rows))} / {len(rows)} 句'})
            return rows
        if kind == 'tts':
            import sherpa_onnx
            model = folder/'chinese-voice'
            if self.model is None:
                # Kaldi FST still uses narrow fopen on Windows; relative ASCII names
                # work even when the containing directory has Chinese characters.
                os.chdir(model)
                vits = sherpa_onnx.OfflineTtsVitsModelConfig(model='model.onnx', lexicon='lexicon.txt',
                    tokens='tokens.txt', dict_dir='dict')
                config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=req.get('threads', 2), provider='cpu'),
                    rule_fsts=','.join(name for name in ('date.fst', 'number.fst') if (model/name).is_file()))
                if not config.validate(): raise ValueError('中文配音模型配置无效')
                self.model = sherpa_onnx.OfflineTts(config)
            out = Path(req['output']); out.mkdir(exist_ok=True)
            result = []
            for i, row in enumerate(req['segments']):
                text = row['zh'].strip()
                if not text: raise ValueError('中文译文为空，请先填写译文')
                generated = self.model.generate(text, sid=0, speed=1.0)
                duration = len(generated.samples)/generated.sample_rate
                end = req['segments'][i+1]['start'] if i+1 < len(req['segments']) else req['duration']
                available = max(.1, end-row['start'])
                speed = min(1.25, max(1, duration/available))
                if speed > 1.01:
                    generated = self.model.generate(text, sid=0, speed=speed)
                    duration = len(generated.samples)/generated.sample_rate
                path = out/f'voice-{i:05d}.wav'
                write_wav(path, generated.samples, generated.sample_rate)
                result.append({**row, 'voice': str(path), 'voice_duration': duration, 'speed': speed,
                               'needs_review': duration > available+.08})
                emit({'progress': f'已配音 {i+1} / {len(req["segments"])} 句'})
            return result
        if kind == 'separate':
            import torch
            torch.set_num_threads(req.get('threads', 2))
            from demucs.hf import load_safetensors_model
            from demucs.apply import apply_model
            import numpy as np
            if self.model is None:
                self.model = load_safetensors_model(folder/'background/955717e8.safetensors').to(device).eval()
            # Audio is chunked by the parent; Demucs further splits inference windows.
            with wave.open(req['audio'], 'rb') as source:
                if source.getsampwidth() != 2 or source.getframerate() != self.model.samplerate or source.getnchannels() != self.model.audio_channels:
                    raise ValueError('音频分离输入必须为 44.1kHz 双声道 PCM16')
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').reshape(-1, source.getnchannels()).T.copy().astype('float32')/32768
            audio = torch.from_numpy(samples)
            mean = audio.mean(0).mean(); std = audio.mean(0).std().clamp_min(1e-6)
            with torch.no_grad():
                stems = apply_model(self.model, ((audio-mean)/std)[None].to(device), device=device, shifts=0, split=True, segment=7, progress=False)[0]
                backing = sum(stems[i] for i, name in enumerate(self.model.sources) if name != 'vocals')*std
            with wave.open(req['output'], 'wb') as out:
                out.setnchannels(self.model.audio_channels); out.setsampwidth(2); out.setframerate(self.model.samplerate)
                out.writeframes((np.clip(backing.cpu().numpy().T, -1, 1)*32767).astype('<i2').tobytes())
            return {'path': req['output']}
        raise ValueError('未知 AI 阶段')


def main():
    engine = Engine()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            emit({'result': engine.call(request)})
        except Exception as exc:
            import re
            error = re.sub(r'https?://\S+', '[组件来源]', str(exc))
            emit({'error': error[-1200:]})


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--convert': convert(sys.argv[2])
    else: main()
