"""Catalog facts and nested, synced source folders remain read-only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dog_post_maker.foundation import read_catalog, matching_folders, stage_media, media_path, sync_catalog, birth_date


class FoundationTests(unittest.TestCase):
    def test_catalog_reads_literals_without_running_colleague_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'app.py'
            source.write_text('''raise RuntimeError("must never execute")
DOGS = [dog("cara", "Cara", "Roemenië", "https://example.test/cara", "Teef", "Jong · geboren 27 april 2023", "Groot", "assets/cara.jpg", dogs="yes", cats="unknown", note="Geduldig baasje nodig.")]
''')
            rows=read_catalog(source)
            self.assertEqual(rows[0]['name'],'Cara')
            self.assertEqual(rows[0]['cats'],'unknown')
            self.assertEqual(rows[0]['dogs'],'yes')

    def test_recursive_media_spaces_case_descriptors_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';root.mkdir()
            external=Path(tmp)/'dog profiles';external.mkdir()
            dog=external/'cArA, pup van Meli (Rescuer)-001'
            (dog/'Foto_s').mkdir(parents=True);(dog/'2026-01-01').mkdir()
            (dog/'Foto_s'/'same.jpg').write_bytes(b'first photo')
            (dog/'2026-01-01'/'same.jpg').write_bytes(b'second photo')
            (dog/'2026-01-01'/'clip.MOV').write_bytes(b'video')
            (external/'Cara (Rescuer).zip').write_bytes(b'zip ignored')
            blank=external/'Blanco dossier - kopie (6)';blank.mkdir();(blank/'photo.jpg').write_bytes(b'ignored')
            with patch.dict(os.environ,{'PHOTO_ROOT':str(external)}):
                self.assertEqual(matching_folders(external,'CARA'),[dog])
                result=stage_media(root,'cara','Cara')
                self.assertEqual((result['photos'],result['videos']),(2,1))
                folder=root/'input'/'cara';manifest=json.loads((folder/'media_sources.json').read_text())
                self.assertEqual(len(manifest['files']),3)
                aliases=[n for n in manifest['files'] if n.endswith('.jpg')]
                self.assertEqual(len(set(aliases)),2)
                self.assertTrue(all((folder/name).is_symlink() for name in aliases))
                self.assertEqual({media_path(folder,n).read_bytes() for n in aliases},{b'first photo',b'second photo'})
                stage_media(root,'cara','Cara')
                self.assertFalse((folder/'.previous_media').exists())
                (folder/'rogue.jpg').symlink_to(external/'Cara (Rescuer).zip')
                with self.assertRaises(ValueError):media_path(folder,'rogue.jpg')
                self.assertEqual((dog/'Foto_s'/'same.jpg').read_bytes(),b'first photo')

    def test_sync_keeps_unknown_compatibility_and_exact_age_qualifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';root.mkdir();external=Path(tmp)/'dog profiles';external.mkdir()
            source=Path(tmp)/'app.py'
            source.write_text('DOGS=[dog("hughie","Hughie","Noord-Macedonië","url","Reu","Jong · geboren rond mei–juni 2024","Middelgroot","image",dogs="yes",children="unknown")]')
            with patch.dict(os.environ,{'PHOTO_ROOT':str(external)}):
                result=sync_catalog(root,source)
            import yaml
            dog=yaml.safe_load((root/'input/hughie/dog.yaml').read_text())
            self.assertEqual(dog['birth_date'],'2024')
            self.assertEqual(dog['birth_date_text'],'Geboren rond mei–juni 2024')
            self.assertIsNone(dog['good_with_children'])
            self.assertIsNone(dog['good_with_cats'])
            self.assertTrue(dog['good_with_dogs'])
            self.assertEqual(result[0]['photos'],0)
            self.assertEqual(birth_date('Pup · geboren 24 april 2026'),'2026-04-24')


if __name__=='__main__':unittest.main()
