"""ImageHash integration, review choices and reversible file operations."""
import json
from pathlib import Path
import random
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from integrations import image_dedup as dedup


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video-desk-dedup-')
        self.base = Path(self.temp.name)
        self.source = self.base / '图片来源'
        self.folder = self.base / '扫描报告'
        self.source.mkdir(); self.folder.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def photo(self, name='original.png', size=(640, 360)):
        image = Image.new('RGB', size, '#e0eadc')
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 30, 280, 200), fill='#12746c')
        draw.ellipse((270, 40, 550, 310), fill='#c59b61')
        draw.line((20, 300, 620, 250), fill='#213142', width=13)
        draw.text((60, 70), 'Video Desk 123456', fill='white')
        image.save(self.source / name)
        return image

    def scan(self, **kwargs):
        return dedup.scan(self.source, self.folder, **kwargs)

    def test_exact_recursive_and_corrupt_images(self):
        self.photo(); (self.source/'sub').mkdir()
        shutil.copy2(self.source/'original.png', self.source/'sub/copy.png')
        (self.source/'broken.jpg').write_bytes(b'bad image')
        (self.source/dedup.QUARANTINE).mkdir()
        shutil.copy2(self.source/'original.png', self.source/dedup.QUARANTINE/'ignored.png')
        report = self.scan()
        self.assertEqual(report['summary'], {'images':2,'groups':1,'duplicates':1,'skipped':1})
        self.assertEqual(report['groups'][0]['kind'], 'exact')
        self.assertEqual(len(list(self.source.rglob('*.png'))),3)

    def test_similar_resized_jpeg_and_different_flat_colors(self):
        image = self.photo()
        image.resize((320,180)).save(self.source/'compressed.jpg', quality=80)
        Image.new('RGB',(300,200),'red').save(self.source/'red.png')
        Image.new('RGB',(300,200),'blue').save(self.source/'blue.png')
        report = self.scan(mode='similar')
        self.assertEqual(report['summary']['duplicates'],1)
        group = report['groups'][0]
        self.assertEqual(group['kind'],'similar')
        items = {i['id']:i for i in report['items']}
        self.assertEqual(items[group['keep']]['relative'],'original.png')
        self.assertEqual({items[i]['relative'] for i in group['members']},{'original.png','compressed.jpg'})

    def test_choice_export_preserves_tree_and_originals(self):
        image=self.photo(); (self.source/'sub').mkdir()
        image.resize((320,180)).save(self.source/'sub/small.jpg')
        Image.new('RGB',(200,100),'red').save(self.source/'unique.png')
        report=self.scan(mode='similar')
        group=report['groups'][0]
        keep=next(i['id'] for i in report['items'] if i['relative']=='sub/small.jpg')
        op=dedup.operate(self.folder,'export',{group['id']:keep},'test')
        self.assertEqual({p.relative_to(op['directory']).as_posix() for p in Path(op['directory']).rglob('*') if p.is_file()}, {'sub/small.jpg','unique.png'})
        self.assertTrue((self.source/'original.png').is_file())
        self.assertEqual(len(list(self.source.rglob('*.*'))),3)

    def test_quarantine_restore_and_collision_refuses_overwrite(self):
        self.photo(); shutil.copy2(self.source/'original.png',self.source/'copy.png')
        report=self.scan(); group=report['groups'][0]
        op=dedup.operate(self.folder,'quarantine',{group['id']:group['keep']},'test')
        entry=op['entries'][0]; original=self.source/entry['source']
        self.assertFalse(original.exists())
        self.assertTrue((Path(op['directory'])/entry['destination']).is_file())
        original.write_bytes(b'new user file')
        with self.assertRaisesRegex(ValueError,'未覆盖'):
            dedup.operate(self.folder,'restore',{},'test')
        self.assertEqual(original.read_bytes(),b'new user file')
        # Preserve the new file elsewhere, as the UI instructs, then restore.
        original.rename(self.source/'new-user-file.saved')
        result=dedup.operate(self.folder,'restore',{},'test')
        self.assertEqual(result['count'],1)
        self.assertEqual(dedup.digest(original),entry['sha256'])
        self.assertEqual(dedup.read_report(self.folder)['operations'][0]['state'],'restored')

    def test_modified_original_invalid_choices_and_path_escape(self):
        self.photo(); shutil.copy2(self.source/'original.png',self.source/'copy.png')
        report=self.scan(); group=report['groups'][0]
        with self.assertRaisesRegex(ValueError,'不属于'):
            dedup.operate(self.folder,'quarantine',{group['id']:'unknown'},'test')
        (self.source/'copy.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'):
            dedup.operate(self.folder,'quarantine',{group['id']:group['keep']},'test')
        self.assertTrue((self.source/'original.png').is_file())
        with self.assertRaises(ValueError): dedup.inside(self.source,'../outside.jpg')

    def test_restore_recovers_rename_interrupted_before_completion_record(self):
        self.photo();shutil.copy2(self.source/'original.png',self.source/'copy.png')
        report=self.scan();group=report['groups'][0]
        real_rename=dedup.os.rename
        def interrupted(source,dest):
            real_rename(source,dest)
            raise OSError('simulated interruption after move')
        with patch.object(dedup.os,'rename',side_effect=interrupted):
            with self.assertRaises(OSError):
                dedup.operate(self.folder,'quarantine',{group['id']:group['keep']},'test')
        self.assertEqual(len(list(self.source.glob('*.png'))),1)
        self.assertEqual(dedup.operate(self.folder,'restore',{},'test')['count'],1)
        self.assertEqual(len(list(self.source.glob('*.png'))),2)

    def test_hash_index_matches_brute_force_and_scan_limit(self):
        randomizer=random.Random(123)
        values=[randomizer.getrandbits(64) for _ in range(1000)]
        index=dedup.HashIndex()
        for i,value in enumerate(values): index.add(value,i)
        for value in values[::37]:
            self.assertEqual(set(index.find(value,8)),{i for i,other in enumerate(values) if (value^other).bit_count()<=8})
        self.photo(); shutil.copy2(self.source/'original.png',self.source/'copy.png')
        report=self.scan(limit=1)
        self.assertTrue(report['truncated']);self.assertEqual(report['summary']['images'],1)


if __name__=='__main__': unittest.main()
