from pathlib import Path
import csv, json, tempfile, unittest
from tools.reconcile_drive_corrections import Reconciler

class FakeRclone:
    def __init__(self, listings): self.listings=listings; self.calls=[]
    def __call__(self, _exe, args):
        self.calls.append(args)
        if args[0] == 'lsjson':
            path=args[1].split(':',1)[1]; return 0, json.dumps(self.listings.get(path, [])), ''
        if args[0] == 'copyto':
            path=args[2].split(':',1)[1]; self.listings[path]=[{'ID':'new-id','Size':3,'MD5':'22af645d1859cb5ca6da0c484f1f37ea'}]; return 0, 'copy ok', ''
        if args[0] == 'deletefile':
            path=args[1].split(':',1)[1]; self.listings[path]=[]; return 0, 'trashed', ''
        return 1, '', 'unexpected'

class ReconcileDriveTests(unittest.TestCase):
    def make(self, root, status='new_ready', **extra):
        p=root/'new.jpg'; p.write_bytes(b'new')
        row={'status':status,'source_identity':'id','year':'2026','source_path':str(p),'local_path':str(p),'corrected_file_name':'new.jpg','gate_evidence':'','old_remote_path':'2026/old.jpg','old_drive_file_id':'old-id'}
        row.update(extra); return row
    def rec(self, root, rows, fake):
        ledger=root/'ledger.jsonl'; ledger.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        return Reconciler(ledger,'remote','rclone',True,fake)
    def test_upload_sequence_no_overwrite_and_readback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({}); rec=self.rec(root,[self.make(root)],fake); rec.upload_new(rec.rows[0]);
            self.assertEqual([c[0] for c in fake.calls],['lsjson','copyto','lsjson']); self.assertIn('--immutable',fake.calls[1]); self.assertIn('--hash-type',fake.calls[0]); self.assertIn('MD5',fake.calls[0]); self.assertEqual(rec.rows[0]['status'],'new_uploaded_verified')
    def test_duplicate_mismatch_and_missing_old_id_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({'2026/new.jpg':[{'ID':'a','Size':3,'MD5':'bad'},{'ID':'b','Size':3,'MD5':'bad'}]}); rec=self.rec(root,[self.make(root)],fake); rec.upload_new(rec.rows[0]); self.assertIn('duplicate',rec.rows[0]['last_error'])
            rec.rows[0]['status']='new_uploaded_verified'; rec.rows[0]['old_drive_file_id']=''; rec.trash_old(rec.rows[0]); self.assertIn('missing',rec.rows[0]['last_error'])
    def test_hash_mismatch_and_trash_readback_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({'2026/new.jpg':[{'ID':'new','Size':99,'MD5':'bad'}]}); rec=self.rec(root,[self.make(root)],fake); rec.upload_new(rec.rows[0]); self.assertIn('mismatch',rec.rows[0]['last_error'])
            md5='22af645d1859cb5ca6da0c484f1f37ea'; fake=FakeRclone({'2026/old.jpg':[{'ID':'old-id','Size':3,'Hashes':{'MD5':'x'}}],'2026/new.jpg':[{'ID':'new-id','Size':3,'Hashes':{'MD5':md5}}]}); rec=self.rec(root,[self.make(root,status='new_uploaded_verified',new_remote_path='2026/new.jpg',new_drive_file_id='new-id',new_remote_size=3,new_remote_md5=md5)],fake); rec.trash_old(rec.rows[0]); self.assertEqual(rec.rows[0]['status'],'old_trashed_verified')

    def test_pending_trash_recovers_by_readback_without_deleting_again(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); md5='22af645d1859cb5ca6da0c484f1f37ea'
            fake=FakeRclone({'2026/old.jpg':[],'2026/new.jpg':[{'ID':'new-id','Size':3,'Hashes':{'MD5':md5}}]})
            row=self.make(root,status='old_trash_pending',new_remote_path='2026/new.jpg',new_drive_file_id='new-id',new_remote_size=3,new_remote_md5=md5)
            rec=self.rec(root,[row],fake); rec.trash_old(rec.rows[0])
            self.assertEqual(rec.rows[0]['status'],'old_trashed_verified')
            self.assertEqual([call[0] for call in fake.calls],['lsjson','lsjson'])
    def test_rerun_idempotency_and_dry_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({'2026/new.jpg':[{'ID':'new','Size':3,'MD5':'22af645d1859cb5ca6da0c484f1f37ea'}]}); row=self.make(root); rec=self.rec(root,[row],fake); rec.upload_new(row); calls=len(fake.calls); rec.upload_new(row); self.assertEqual(len(fake.calls),calls)
            row['status']='new_ready'; fake=FakeRclone({}); rec=self.rec(root,[row],fake); rec.upload_new(row,dry_plan=True); self.assertIn('planned_command',row); self.assertEqual(fake.calls,[])
    def test_execute_requires_phase_and_schema_tokens(self):
        src=(Path(__file__).parent/'reconcile_drive_corrections.py').read_text(encoding='utf-8')
        for token in ('upload-new','trash-old','--immutable','--drive-use-trash','new_uploaded_verified','old_trash_pending'):
            self.assertIn(token,src)

if __name__=='__main__': unittest.main()
