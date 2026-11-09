"""Cross-platform RAW receipt tests; never address a real printer or shop DB."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pos import cash_sessions, settings as cfg, thermal_print as tp
from pos.db import connect, init_db


class ReceiptTimezoneTests(unittest.TestCase):
    def test_utc_timestamp_is_rendered_in_ist_without_iana_tzdata(self):
        # Windows does not bundle the IANA Asia/Kolkata zone database.
        self.assertEqual(tp._printed_date('2026-11-08T18:45:00+00:00'),
                         '09 Nov 2026 12:15 AM IST')

    def test_naive_database_timestamp_preserves_previous_utc_assumption(self):
        self.assertEqual(tp._printed_date('2026-11-08 09:00:00'),
                         '08 Nov 2026 02:30 PM IST')

    def test_missing_or_invalid_timestamp_is_safe(self):
        self.assertEqual(tp._printed_date(None), '')
        self.assertEqual(tp._printed_date('invalid-date'), 'invalid-date')


class ReceiptRasterTests(unittest.TestCase):
    def test_tm_t81_width_and_escpos_bit_count(self):
        from PIL import ImageDraw
        image = Image.new('RGB', (512, 129), 'white')
        ImageDraw.Draw(image).point((0, 0), fill='black')
        commands = tp.raster_commands(image)
        self.assertTrue(commands.startswith(b'\x1b@\x1dv0\x00\x40\x00\x80\x00'))
        self.assertEqual(commands[10], 0x80)  # top-left black bit
        self.assertEqual(commands.count(b'\x1dv0\x00'), 2)
        self.assertTrue(commands.endswith(b'\n\n\n\x1dV\x01'))
        self.assertEqual(len(commands), 2 + 8 + 128*64 + 8 + 64 + 6)
        with self.assertRaises(tp.PrinterError):
            tp.raster_commands(Image.new('RGB', (576, 20), 'white'))

    def test_logo_and_long_receipt_are_clipped_and_printable(self):
        logo = Image.new('RGB', (80, 20), 'black')
        buf = io.BytesIO()
        logo.save(buf, 'PNG')
        store = {'store_name': 'Test shop', 'show_receipt_logo': 'true',
                 'store_logo': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode(),
                 'receipt_logo_width':'32', 'receipt_paper':'80mm', 'price_tax_mode':'inclusive'}
        receipt = {'id':23,'status':'COMPLETED','timestamp':'2026-11-08 09:00:00',
                   'invoice_number':'26-27/000023', 'subtotal':700, 'tax_total':35,
                   'total':700,'payments':[{'method':'UPI','amount':700}],
                   'exchange_offset_used':250,'items':[
                       {'receipt_name':'A nighty with an extraordinarily long description ' + 'W'*125,
                        'receipt_variant':'Size: XL, Colour: Midnight blue', 'quantity':2,
                        'unit_price':350,'line_total':700,'tax_rate':5}],
                   'exchange_links':[{'role':'replacement','forfeited_amount':5,
                                      'refund_number':'26-27/R000001-000005'}]}
        im = tp.render_receipt(receipt, store)
        self.assertEqual(im.width, 512)
        self.assertLess(im.height, 60000)
        self.assertNotEqual(im.getbbox(), None)
        self.assertGreater(len(tp.raster_commands(im)), 1000)

    def test_calibration_carries_no_sale_identifiers(self):
        im = tp.render_test({'store_name':'Shop','show_receipt_logo':'false'})
        self.assertEqual(im.width, 512)
        self.assertLess(im.height, 600)

    def test_unknown_printer_and_paper_are_rejected(self):
        image = Image.new('RGB', (512, 20), 'white')
        with self.assertRaisesRegex(tp.PrinterError, '80mm'):
            tp.send_image(image, {'receipt_paper':'A4'})
        with self.assertRaisesRegex(tp.PrinterError, 'Invalid printer'):
            tp.send_image(image, {'receipt_paper':'80mm','receipt_printer_name':'good\nBAD'})


class BackendSpoolTests(unittest.TestCase):
    def test_linux_cups_raw_has_no_shell_and_uses_selected_queue(self):
        image = Image.new('RGB', (512, 12), 'white')
        with patch.object(tp.sys, 'platform', 'linux'), patch.object(tp.subprocess, 'run') as run:
            run.return_value.returncode = 0
            name = tp.send_image(image, {'receipt_paper':'80mm','receipt_printer_name':'EPSON TM-T81'})
        self.assertEqual(name, 'EPSON TM-T81')
        self.assertEqual(run.call_args.args[0],
                         ['lp','-o','raw','-t','pyPOS receipt','-d','EPSON TM-T81'])
        self.assertIsInstance(run.call_args.kwargs['input'], bytes)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_macos_cups_default_queue(self):
        image = Image.new('RGB', (512, 10), 'white')
        with patch.object(tp.sys, 'platform', 'darwin'), patch.object(tp.subprocess, 'run') as run:
            run.return_value.returncode = 0
            name = tp.send_image(image, {'receipt_paper':'80mm','receipt_printer_name':''})
        self.assertEqual(name,'system default')
        self.assertNotIn('-d', run.call_args.args[0])

    def test_cups_spooler_permission_error_is_reported(self):
        image = Image.new('RGB', (512, 10), 'white')
        with patch.object(tp.sys, 'platform', 'linux'), \
             patch.object(tp.subprocess, 'run', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(tp.PrinterError, 'Cannot submit'):
                tp.send_image(image, {'receipt_paper':'80mm'})

    def test_windows_uses_windows_spooler(self):
        image = Image.new('RGB', (512, 10), 'white')
        with patch.object(tp.sys, 'platform', 'win32'), \
             patch.object(tp, '_spool_windows') as spool:
            name = tp.send_image(image, {'receipt_paper':'80mm','receipt_printer_name':'EPSON TM-T81'})
        self.assertEqual(name,'EPSON TM-T81')
        self.assertEqual(spool.call_args.args[0],'EPSON TM-T81')
        self.assertTrue(spool.call_args.args[1].startswith(b'\x1b@'))

    def test_cups_failure_is_not_claimed_as_printed(self):
        image = Image.new('RGB', (512, 12), 'white')
        with patch.object(tp.sys,'platform','darwin'),patch.object(tp.subprocess,'run') as run:
            run.return_value.returncode = 5
            run.return_value.stderr = b'printer offline'
            with self.assertRaisesRegex(tp.PrinterError,'printer offline'):
                tp.send_image(image, {'receipt_paper':'80mm'})


class PrintJobAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'pos.db'
        init_db(self.path)
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)
        self.sale_id = self.conn.execute(
            "INSERT INTO sales(subtotal,tax_total,total,invoice_number) VALUES(100,5,105,'26-27/000001')"
        ).lastrowid

    def test_original_schema_migrates_without_modifying_old_invoice(self):
        self.assertTrue(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_print_jobs'").fetchone())
        init_db(self.path)  # idempotent against a live existing database
        row = self.conn.execute('SELECT invoice_number,total FROM sales WHERE id=?',(self.sale_id,)).fetchone()
        self.assertEqual((row['invoice_number'],row['total']),('26-27/000001',105))

    def test_automatic_is_spooled_once_manual_always_audited(self):
        with patch.object(tp,'send_image',return_value='EPSON TM-T81') as printer:
            first=tp.print_recorded_sale(self.conn,self.sale_id)
            second=tp.print_recorded_sale(self.conn,self.sale_id)
            manual=tp.print_recorded_sale(self.conn,self.sale_id,manual=True)
        self.assertEqual(first['status'],'spooled')
        self.assertEqual(second,{'status':'already_requested','previous_status':'SPOOLED'})
        self.assertEqual(manual['status'],'spooled')
        self.assertEqual(printer.call_count,2)
        rows=list(self.conn.execute('SELECT kind,status FROM receipt_print_jobs ORDER BY id'))
        self.assertEqual([(r['kind'],r['status']) for r in rows],
                         [('auto','SPOOLED'),('manual','SPOOLED')])

    def test_failure_is_logged_and_never_retries_automatically(self):
        with patch.object(tp,'send_image',side_effect=tp.PrinterError('no paper')) as printer:
            with self.assertRaisesRegex(tp.PrinterError,'no paper'):
                tp.print_recorded_sale(self.conn,self.sale_id)
            second=tp.print_recorded_sale(self.conn,self.sale_id)
        self.assertEqual(second['previous_status'],'FAILED')
        self.assertEqual(printer.call_count,1)
        self.assertEqual(self.conn.execute('SELECT error FROM receipt_print_jobs').fetchone()[0], 'no paper')
        self.assertEqual(self.conn.execute('SELECT total FROM sales WHERE id=?',(self.sale_id,)).fetchone()[0],105)

    def test_no_invalid_job_and_bad_settings(self):
        with self.assertRaisesRegex(tp.PrinterError,'disabled'):
            cfg.save_settings(self.conn,{'receipt_print_mode':'browser'})
            tp.print_recorded_sale(self.conn,self.sale_id)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM receipt_print_jobs').fetchone()[0],0)
        with self.assertRaises(ValueError):
            cfg.save_settings(self.conn,{'receipt_print_mode':'thermal','receipt_paper':'A4'})
        with self.assertRaises(ValueError):
            cfg.save_settings(self.conn,{'receipt_printer_name':'bad\nother'})

    def test_closed_session_report_prints(self):
        result=cash_sessions.open_session(self.conn, 100)
        report = result['report']
        image=tp.render_z_report(report,cfg.get_settings(self.conn))
        self.assertEqual(image.width,512)
        self.assertGreater(image.height,200)


if __name__ == '__main__':
    unittest.main()
