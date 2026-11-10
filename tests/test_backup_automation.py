"""Automatic snapshot, replication, migration and multi-counter regressions."""
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from pos import backups, cash_sessions, db, settings


class AutomaticBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "store.db"
        db.init_db(self.path)
        self.bdir = self.path.parent / "backups"
        conn = db.connect(self.path)
        try:
            self.options = settings.get_settings(conn)
        finally:
            conn.close()  # Windows cannot delete an open database file.

    def _read_db(self, path, key):
        con = sqlite3.connect(str(path))
        try:
            return con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        finally:
            con.close()

    def test_default_10min_30days_and_three_remote_staged_copies(self):
        self.assertEqual(self.options['auto_backup_interval_minutes'], '10')
        self.assertEqual(self.options['backup_retention_days'], '30')
        sched = backups.BackupScheduler(lambda: self.options, self.path)
        start = time.time()
        self.assertAlmostEqual(sched.next_run(self.options, start), start + 600, delta=1)
        self.assertTrue(self.options['backup_replication_enabled'] == 'true')
        for i in range(4):
            con = db.connect(self.path)
            try:
                settings.save_settings(con, {'store_name': f'Shop{i}'})
            finally:
                con.close()
            result = backups.run_backup_cycle(self.options, self.path)
            self.assertTrue(result['replication_staged'], result)
            staged = backups._sync_outbox(self.path) / result['name']
            backups.validate_database(staged)
            self.assertEqual(self._read_db(staged, 'store_name'), (f'Shop{i}',))
        self.assertEqual(len(list(backups._sync_outbox(self.path).glob('pypos-*.db'))), 3)
        self.assertFalse(any(f.name == 'store.db' for f in backups._sync_outbox(self.path).iterdir()))

    def test_wal_snapshot_includes_committed_sales_settings_without_checkpoint(self):
        live = db.connect(self.path)
        try:
            live.execute('PRAGMA journal_mode=WAL')
            settings.save_settings(live, {'store_name': 'Latest live value'})
            wal = Path(str(self.path) + '-wal')
            before = wal.stat().st_size
            self.assertGreater(before, 0)
            backups.run_backup_cycle(self.options, self.path)
            self.assertEqual(wal.stat().st_size, before)
            name = backups.list_backups(self.bdir)[0]['name']
            self.assertEqual(self._read_db(self.bdir / name, 'store_name'), ('Latest live value',))
        finally:
            live.close()

    def test_outbox_failure_never_undoes_valid_local_snapshot(self):
        with mock.patch.object(backups, 'replicate_snapshot', side_effect=backups.BackupError('offline')):
            result = backups.run_backup_cycle(self.options, self.path)
        self.assertFalse(result['replication_staged'])
        self.assertEqual(result['replication_error'], 'offline')
        backups.validate_database(self.bdir / result['name'])

    def test_atomic_outbox_failure_leaves_no_final_copy(self):
        name = backups.create_backup(self.path)
        path = self.bdir / name
        with mock.patch.object(backups, 'validate_database', side_effect=[None, backups.BackupError('corrupt copy')]):
            with self.assertRaises(backups.BackupError):
                backups.replicate_snapshot(path, self.path)
        self.assertEqual(list(backups._sync_outbox(self.path).glob('*.db')), [])

    def test_tiered_history_and_30_day_age_ceiling(self):
        now = datetime.now(timezone.utc).replace(hour=12, minute=30, second=0, microsecond=0)
        simulated = []
        for delta in (timedelta(minutes=10), timedelta(hours=30), timedelta(hours=30, minutes=10),
                      timedelta(days=12), timedelta(days=12, hours=1), timedelta(days=31)):
            timestamp = now - delta
            item = {'name': 'pypos-' + timestamp.strftime('%Y-%m-%d-%H%M%S') + '-000.db',
                    'created_at': timestamp.replace(tzinfo=None).isoformat(timespec='seconds'), 'reason': ''}
            simulated.append(item)
        simulated.sort(key=lambda d: d['name'], reverse=True)
        keep = backups._retention_candidates(simulated, {'backup_retention_days': '30'})
        self.assertEqual(len(keep), 3)
        self.assertNotIn(simulated[-1], keep)
        self.assertIn(simulated[0], keep)

    def test_pre_migration_snapshots_are_created_once_and_restoreable(self):
        con = sqlite3.connect(str(self.path))
        try:
            con.execute('PRAGMA user_version = 0')  # Old release database.
            con.execute("INSERT INTO settings(key,value) VALUES('pre_migrate','preserved')")
            con.commit()
        finally:
            con.close()
        db.init_db(self.path)
        pre = [x for x in backups.list_backups(self.bdir) if x['reason'] == 'pre_migration']
        self.assertEqual(len(pre), 1)
        self.assertEqual(self._read_db(self.bdir / pre[0]['name'], 'pre_migrate'), ('preserved',))
        db.init_db(self.path)
        pre = [x for x in backups.list_backups(self.bdir) if x['reason'] == 'pre_migration']
        self.assertEqual(len(pre), 1)

    def test_failed_pre_migration_backup_prevents_schema_changes(self):
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute('PRAGMA user_version = 0')
            conn.commit()
        finally:
            conn.close()
        with mock.patch.object(backups, 'create_backup', side_effect=backups.BackupError('disk full')):
            with self.assertRaisesRegex(backups.BackupError, 'disk full'):
                db.init_db(self.path)
        con = sqlite3.connect(str(self.path))
        try:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 0)
        finally:
            con.close()

    def test_day_end_close_commits_and_creates_immutable_snapshot(self):
        from pos.app import create_app
        from pos import staff
        con = db.connect(self.path)
        try:
            staff.add_user(con, 'owner1', 'Owner', 'admin', '12345678')
        finally:
            con.close()
        app = create_app(self.path)
        app.config['TESTING'] = True
        client = app.test_client()
        self.assertEqual(client.post('/staff-login', data={
            'username': 'owner1', 'pin': '12345678'}).status_code, 302)
        self.assertEqual(client.post('/api/cash-sessions/open', json={
            'opening_float': 100}).status_code, 201)
        closed = client.post('/api/cash-sessions/close', json={'counted_cash': 100})
        self.assertEqual(closed.status_code, 200, closed.get_data(as_text=True))
        self.assertIsNotNone(closed.json['session']['closed_at'])
        self.assertEqual(len([i for i in backups.list_backups(self.bdir)
                              if i['reason'] == 'day_end']), 1)
        self.assertIn('backup', closed.json)
        backups.validate_database(self.bdir / closed.json['backup']['name'])

    def test_day_end_backup_failure_never_reopens_closed_day(self):
        from pos.app import create_app
        from pos import staff
        con = db.connect(self.path)
        try:
            staff.add_user(con, 'owner2', 'Owner', 'admin', '12345678')
        finally:
            con.close()
        app = create_app(self.path)
        app.config['TESTING'] = True
        client = app.test_client()
        self.assertEqual(client.post('/staff-login', data={
            'username': 'owner2', 'pin': '12345678'}).status_code, 302)
        self.assertEqual(client.post('/api/cash-sessions/open', json={
            'opening_float': 0}).status_code, 201)
        with mock.patch.object(backups, 'run_backup_cycle',
                               side_effect=backups.BackupError('disk full')):
            closed = client.post('/api/cash-sessions/close', json={'counted_cash': 0})
        self.assertEqual(closed.status_code, 200)
        self.assertIsNotNone(closed.json['session']['closed_at'])
        self.assertIn('backup_warning', closed.json)
        self.assertIsNone(client.get('/api/cash-sessions/current').json)

    def test_backup_setting_validation(self):
        con = db.connect(self.path)
        try:
            for vals in ({'auto_backup_interval_minutes': '0'},
                         {'backup_retention_days': '366'},
                         {'backup_replication_enabled': 'maybe'}):
                with self.assertRaises(ValueError):
                    settings.save_settings(con, vals)
            settings.save_settings(con, {'backup_day_end_enabled': 'false'})
        finally:
            con.close()

    def test_restore_from_replication_copy_and_no_live_file_sync(self):
        con = db.connect(self.path)
        try:
            settings.save_settings(con, {'store_name': 'Before snapshot'})
        finally:
            con.close()
        result = backups.run_backup_cycle(self.options, self.path)
        copied = backups._sync_outbox(self.path) / result['name']
        con = db.connect(self.path)
        try:
            settings.save_settings(con, {'store_name': 'Changed since backup'})
        finally:
            con.close()
        backups._swap_live(copied, self.path)
        self.assertEqual(self._read_db(self.path, 'store_name'), ('Before snapshot',))


class ManagedUpdateSnapshotTests(unittest.TestCase):
    def test_failed_pre_update_sync_leaves_no_published_file(self):
        from pos import managed_launcher as ml
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            path = store / 'pypos.db'
            db.init_db(path)
            with mock.patch.object(ml, 'default_data_dir', return_value=store), \
                 mock.patch.object(ml.os, 'fsync', side_effect=OSError('disk error')):
                with self.assertRaisesRegex(OSError, 'disk error'):
                    ml._pre_update_backup('2.0.0', '2.1.0')
            out = store / 'backups'
            self.assertEqual(list(out.glob('pre-update-*.db')), [])
            self.assertEqual(list(out.glob('.tmp-*.db')), [])


class SharedTerminalTests(unittest.TestCase):
    def test_two_terminals_use_one_server_database(self):
        from pos.app import create_app
        from pos import staff
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'central.db'
            db.init_db(path)
            con = db.connect(path)
            try:
                staff.add_user(con, 'owner1', 'Owner', 'admin', '12345678')
            finally:
                con.close()
            con = db.connect(path)
            try:
                settings.save_settings(con, {'lan_access_enabled': 'true'})
                con.execute("INSERT INTO product_groups(name) VALUES('Garments')")
                con.execute("INSERT INTO products(group_id,SKU,price,cost_price,tax_rate,quantity) "
                            "VALUES(1,'TEST',100,30,0,5)")
            finally:
                con.close()
            app = create_app(path, extra_hosts=('*',))  # Explicit LAN mode.
            app.config['TESTING'] = True
            terminal_a = app.test_client()
            terminal_b = app.test_client()
            for client in (terminal_a, terminal_b):
                login = client.post('/staff-login', data={'username': 'owner1', 'pin': '12345678'},
                                    base_url='http://192.168.1.50:8420')
                self.assertEqual(login.status_code, 302)
            opened = terminal_a.post('/api/cash-sessions/open', json={'opening_float': 250},
                                     base_url='http://192.168.1.50:8420')
            self.assertEqual(opened.status_code, 201, opened.get_data(as_text=True))
            seen = terminal_b.get('/api/cash-sessions/current', base_url='http://192.168.1.50:8420')
            self.assertEqual(seen.status_code, 200)
            self.assertEqual(seen.json['id'], opened.json['session']['id'])
            duplicate = terminal_b.post('/api/cash-sessions/open', json={'opening_float': 0},
                                        base_url='http://192.168.1.50:8420')
            self.assertEqual(duplicate.status_code, 409)
            payload = {'items': [{'product_id': 1, 'quantity': 1}],
                       'payments': [{'method': 'CASH', 'amount': 100}]}
            sale_a = terminal_a.post('/api/sales', json=payload,
                                     base_url='http://192.168.1.50:8420')
            sale_b = terminal_b.post('/api/sales', json=payload,
                                     base_url='http://192.168.1.50:8420')
            self.assertEqual(sale_a.status_code, 201, sale_a.get_data(as_text=True))
            self.assertEqual(sale_b.status_code, 201, sale_b.get_data(as_text=True))
            self.assertNotEqual(sale_a.json['id'], sale_b.json['id'])
            con = db.connect(path)
            try:
                self.assertEqual(con.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0], 3)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 2)
            finally:
                con.close()

    def test_lan_setting_is_opt_in_and_controls_installed_startup(self):
        from pos import cli
        from pos.cli import PortBusyError
        # A separate disposable store is used because this class has no setUp.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.db'
            db.init_db(path)
            con = db.connect(path)
            try:
                settings.save_settings(con, {'lan_access_enabled': 'true'})
            finally:
                con.close()
            with mock.patch.object(cli, '_port_free', return_value=False):
                with self.assertRaises(PortBusyError) as ctx:
                    cli._run(['--db', str(path), '--no-browser'])
            self.assertIn('0.0.0.0', str(ctx.exception))
            con = db.connect(path)
            try:
                settings.save_settings(con, {'lan_access_enabled': 'false'})
            finally:
                con.close()
            with mock.patch.object(cli, '_port_free', return_value=False):
                with self.assertRaises(PortBusyError) as ctx:
                    cli._run(['--db', str(path), '--no-browser'])
            self.assertIn('127.0.0.1', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
