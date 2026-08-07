from pathlib import Path
import csv, json, subprocess, sys, tempfile, unittest
from unittest.mock import patch
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from tools.reconcile_drive_corrections import Reconciler, default_runner, run_phase

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

class MissingPathErrorsRclone(FakeRclone):
    def __call__(self, _exe, args):
        self.calls.append(args)
        if args[0] == 'lsjson':
            path=args[1].split(':',1)[1]
            if path not in self.listings:
                return 3, '', 'ERROR : error listing: directory not found'
            return 0, json.dumps(self.listings[path]), ''
        return super().__call__(_exe, args)

class FlatYearRclone:
    def __init__(self, entries): self.entries={entry['Name']:dict(entry) for entry in entries}; self.calls=[]
    def __call__(self, _exe, args):
        self.calls.append(args)
        if args[0] == 'lsjson' and args[1].endswith(':2026'):
            return 0, json.dumps(list(self.entries.values())), ''
        if args[0] == 'deletefile':
            name=args[1].split(':',1)[1].rsplit('/',1)[-1]
            self.entries.pop(name,None)
            return 0, 'trashed', ''
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

    def test_pending_trash_treats_rclone_missing_path_as_absent_then_verifies_new(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); md5='22af645d1859cb5ca6da0c484f1f37ea'
            fake=MissingPathErrorsRclone({'2026/new.jpg':[{'ID':'new-id','Size':3,'Hashes':{'MD5':md5}}]})
            row=self.make(root,status='old_trash_pending',new_remote_path='2026/new.jpg',new_drive_file_id='new-id',new_remote_size=3,new_remote_md5=md5)
            rec=self.rec(root,[row],fake); rec.trash_old(rec.rows[0])
            self.assertEqual(rec.rows[0]['status'],'old_trashed_verified')
            self.assertEqual([call[0] for call in fake.calls],['lsjson','lsjson'])

    def test_batch_trash_uses_one_year_snapshot_before_and_after(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); md5='22af645d1859cb5ca6da0c484f1f37ea'
            entries=[]; rows=[]
            for index in (1,2):
                entries.extend([
                    {'Name':f'old-{index}.jpg','ID':f'old-{index}','Size':3,'Hashes':{'MD5':'old'}},
                    {'Name':f'new-{index}.jpg','ID':f'new-{index}','Size':3,'Hashes':{'MD5':md5}},
                ])
                rows.append(self.make(
                    root,
                    status='new_uploaded_verified',
                    old_remote_path=f'2026/old-{index}.jpg',
                    old_drive_file_id=f'old-{index}',
                    new_remote_path=f'2026/new-{index}.jpg',
                    new_drive_file_id=f'new-{index}',
                    new_remote_size=3,
                    new_remote_md5=md5,
                ))
            fake=FlatYearRclone(entries); rec=self.rec(root,rows,fake)
            rec.trash_old_batch(rec.rows)
            self.assertEqual([row['status'] for row in rec.rows],['old_trashed_verified']*2)
            self.assertEqual([call[0] for call in fake.calls],['lsjson','deletefile','deletefile','lsjson'])

    def test_lowercase_rclone_md5_key_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); md5='22af645d1859cb5ca6da0c484f1f37ea'
            fake=MissingPathErrorsRclone({'2026/new.jpg':[{'ID':'new-id','Size':3,'Hashes':{'md5':md5}}]})
            row=self.make(root,status='old_trash_pending',new_remote_path='2026/new.jpg',new_drive_file_id='new-id',new_remote_size=3,new_remote_md5=md5,last_error='stale',last_error_at='old',planned_command=['old'])
            rec=self.rec(root,[row],fake); rec.trash_old(rec.rows[0])
            self.assertEqual(rec.rows[0]['status'],'old_trashed_verified')
            self.assertNotIn('last_error',rec.rows[0])
            self.assertNotIn('planned_command',rec.rows[0])

    def test_discover_old_is_read_only_and_unchanged_name_never_trashes_itself(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); md5='22af645d1859cb5ca6da0c484f1f37ea'
            fake=FakeRclone({'2026/new.jpg':[{'ID':'same-id','Size':3,'Hashes':{'MD5':md5}}]})
            row=self.make(root,old_remote_path='2026/new.jpg',old_drive_file_id='')
            rec=self.rec(root,[row],fake); rec.discover_old(rec.rows[0])
            self.assertEqual(rec.rows[0]['old_drive_file_id'],'same-id')
            self.assertEqual([call[0] for call in fake.calls],['lsjson'])
            rec.upload_new(rec.rows[0]); self.assertEqual(rec.rows[0]['status'],'unchanged_remote_verified')
            calls=len(fake.calls); rec.trash_old(rec.rows[0]); self.assertEqual(len(fake.calls),calls)
    def test_rerun_idempotency_and_dry_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({'2026/new.jpg':[{'ID':'new','Size':3,'MD5':'22af645d1859cb5ca6da0c484f1f37ea'}]}); row=self.make(root); rec=self.rec(root,[row],fake); rec.upload_new(row); calls=len(fake.calls); rec.upload_new(row); self.assertEqual(len(fake.calls),calls)
            row['status']='new_ready'; fake=FakeRclone({}); rec=self.rec(root,[row],fake); rec.upload_new(row,dry_plan=True); self.assertIn('planned_command',row); self.assertEqual(fake.calls,[])
    def test_upload_new_hard_rejects_any_non_ready_status(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({}); row=self.make(root,status='detected')
            rec=self.rec(root,[row],fake); rec.upload_new(rec.rows[0])
            self.assertEqual(fake.calls,[])
            self.assertEqual(rec.rows[0]['status'],'detected')
            self.assertIn('status=new_ready',rec.rows[0]['last_error'])

    def test_phase_runner_skips_deferred_legacy_rows_without_remote_calls_or_errors(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fake=FakeRclone({}); row=self.make(root,status='detected',old_drive_file_id='')
            rec=self.rec(root,[row],fake)
            for phase in ('discover-old','upload-new','trash-old'):
                run_phase(rec,phase,dry_plan=False)
            self.assertEqual(fake.calls,[])
            self.assertEqual(rec.rows[0]['status'],'detected')
            self.assertNotIn('last_error',rec.rows[0])

    def test_default_runner_has_a_bounded_remote_timeout(self):
        with patch(
            'tools.reconcile_drive_corrections.subprocess.run',
            side_effect=subprocess.TimeoutExpired(['rclone', 'lsjson'], 180),
        ):
            rc, out, err = default_runner('rclone', ['lsjson', 'remote:file'])
        self.assertEqual(rc, 124)
        self.assertEqual(out, '')
        self.assertIn('timed out', err)

    def test_execute_requires_phase_and_schema_tokens(self):
        src=(Path(__file__).parent/'reconcile_drive_corrections.py').read_text(encoding='utf-8')
        for token in ('discover-old','upload-new','trash-old','--immutable','--drive-use-trash','new_uploaded_verified','unchanged_remote_verified','old_trash_pending'):
            self.assertIn(token,src)

if __name__=='__main__': unittest.main()
