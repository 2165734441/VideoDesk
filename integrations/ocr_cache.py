"""Bounded reuse of exactly identical OCR recognition inputs.

RapidOCR's pinned TextRecognizer calls session(batch) immediately followed by
postprocess_op(predictions, ...). Delay that inference until decoding so the
cache key can include both the complete normalized tensor and decoder options.
Detection, cropping, orientation and normalization still run for every frame.
Only decoded small results are retained, never images or model tensors.
"""
from collections import OrderedDict
import copy
import hashlib
import json
import sys


def _size(value):
    if isinstance(value,dict):return sys.getsizeof(value)+sum(_size(k)+_size(v) for k,v in value.items())
    if isinstance(value,(list,tuple)):return sys.getsizeof(value)+sum(map(_size,value))
    return sys.getsizeof(value)


class _Pending:
    def __init__(self,batch):self.batch=batch


class RecognitionMemo:
    def __init__(self,session,decode,max_bytes=8*1024*1024,max_entries=2048):
        self.session=session;self.decoder=decode;self.max_bytes=max_bytes;self.max_entries=max_entries
        self.cache=OrderedDict();self.bytes=0;self.hits=0;self.misses=0

    def infer(self,batch):return _Pending(batch)

    def decode(self,pending,*args,**kwargs):
        if not isinstance(pending,_Pending):return self.decoder(pending,*args,**kwargs)
        batch=pending.batch
        # No approximate matching: even a single normalized pixel, batch order,
        # shape, dtype or decoder option change requires fresh inference.
        digest=hashlib.sha256(batch.tobytes())
        digest.update(str((batch.shape,str(batch.dtype))).encode())
        digest.update(json.dumps([args,kwargs],sort_keys=True,separators=(',',':')).encode())
        key=digest.digest()
        if key in self.cache:
            self.hits+=1;value,_=self.cache[key];self.cache.move_to_end(key)
            return copy.deepcopy(value)
        self.misses+=1;value=self.decoder(self.session(batch),*args,**kwargs)
        cost=_size(value)+sys.getsizeof(key)+160
        if cost<=self.max_bytes and self.max_entries>0:
            while self.cache and (self.bytes+cost>self.max_bytes or len(self.cache)>=self.max_entries):
                _,(_,old)=self.cache.popitem(last=False);self.bytes-=old
            self.cache[key]=(copy.deepcopy(value),cost);self.bytes+=cost
        return value


def install(engine):
    """Adapt the bundled RapidOCR recognizer without changing its algorithms."""
    recognizer=engine._load_rec_model()
    memo=RecognitionMemo(recognizer.session,recognizer.postprocess_op)
    recognizer.session=memo.infer;recognizer.postprocess_op=memo.decode
    return memo
