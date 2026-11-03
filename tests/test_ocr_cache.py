from types import SimpleNamespace
import unittest
import numpy as np
from integrations.ocr_cache import RecognitionMemo


class RecognitionReuse(unittest.TestCase):
    def make(self,**limits):
        calls=[]
        def model(batch):calls.append(batch.copy());return float(batch.sum())
        def decoder(value,*args,**kwargs):return [[str(value),.99]],[None]
        return RecognitionMemo(model,decoder,**limits),calls

    def test_identical_model_inputs_reuse_without_aliasing_results(self):
        memo,calls=self.make();batch=np.zeros((2,3,48,320),dtype=np.float32)
        expected=memo.decode(memo.infer(batch),False,wh_ratio_list=[1.,2.])
        actual=memo.decode(memo.infer(batch.copy()),False,wh_ratio_list=[1.,2.])
        self.assertEqual(actual,expected);self.assertEqual(len(calls),1)
        actual[0][0][0]='mutated'
        self.assertEqual(memo.decode(memo.infer(batch),False,wh_ratio_list=[1.,2.]),expected)

    def test_one_pixel_shape_dtype_and_decoder_changes_always_recompute(self):
        memo,calls=self.make();batch=np.zeros((1,2),dtype=np.float32)
        memo.decode(memo.infer(batch),False)
        changed=batch.copy();changed[0,0]=1
        for image,word in [(changed,False),(batch.reshape(2,1),False),(batch.astype(np.int32),False),(batch,True)]:
            memo.decode(memo.infer(image),word)
        self.assertEqual(len(calls),5)

    def test_bounded_lru_and_oversized_results_do_not_grow_memory(self):
        memo,calls=self.make(max_entries=2,max_bytes=4096)
        for i in range(100):memo.decode(memo.infer(np.array([i],dtype=np.float32)),False)
        self.assertLessEqual(len(memo.cache),2);self.assertLessEqual(memo.bytes,4096)
        tiny,_=self.make(max_bytes=1)
        tiny.decode(tiny.infer(np.array([1])),False)
        self.assertFalse(tiny.cache);self.assertEqual(tiny.bytes,0)


if __name__=='__main__':unittest.main()
