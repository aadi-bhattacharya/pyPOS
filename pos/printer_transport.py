"""Platform print-spooler integration for ESC/POS thermal printers.

Windows uses the native RAW spooler; Unix-like systems submit raw bytes to
CUPS. This module deliberately performs no invoice, payment or DB work.
"""
import ctypes
import subprocess
import sys


class PrinterError(RuntimeError):
    """Printer unavailable or refused the spool job; sale remains recorded."""


def _windows_default_printer():
    from ctypes import wintypes
    spool = ctypes.WinDLL('winspool.drv', use_last_error=True)
    spool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    spool.GetDefaultPrinterW.restype = wintypes.BOOL
    count = wintypes.DWORD()
    spool.GetDefaultPrinterW(None, ctypes.byref(count))
    if count.value == 0:
        raise PrinterError('No default printer. Configure an Epson TM-T81 printer in Windows.')
    buf = ctypes.create_unicode_buffer(count.value)
    if not spool.GetDefaultPrinterW(buf, ctypes.byref(count)):
        raise PrinterError('Cannot identify the Windows default printer')
    return buf.value


def _spool_windows(printer_name, payload):
    from ctypes import wintypes
    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR),
                    ('pDatatype', wintypes.LPWSTR)]
    spool = ctypes.WinDLL('winspool.drv', use_last_error=True)
    spool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    spool.OpenPrinterW.restype = wintypes.BOOL
    spool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    spool.StartDocPrinterW.restype = wintypes.DWORD
    spool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    spool.StartPagePrinter.restype = wintypes.BOOL
    spool.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD)]
    spool.WritePrinter.restype = wintypes.BOOL
    spool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    spool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    spool.ClosePrinter.argtypes = [wintypes.HANDLE]
    handle = wintypes.HANDLE()
    if not spool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise PrinterError(f'Cannot open Windows printer {printer_name!r} (error {ctypes.get_last_error()})')
    try:
        doc = DOC_INFO_1('pyPOS receipt', None, 'RAW')
        if not spool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise PrinterError(f'Windows spooler rejected receipt (error {ctypes.get_last_error()})')
        try:
            if not spool.StartPagePrinter(handle):
                raise PrinterError(f'Windows printer could not start a page (error {ctypes.get_last_error()})')
            try:
                written = wintypes.DWORD()
                buffer = ctypes.create_string_buffer(payload)
                if not spool.WritePrinter(handle, buffer, len(payload), ctypes.byref(written)):
                    raise PrinterError(f'Windows printer write failed (error {ctypes.get_last_error()})')
                if written.value != len(payload):
                    raise PrinterError(f'Windows printer accepted {written.value}/{len(payload)} bytes')
            finally:
                spool.EndPagePrinter(handle)
        finally:
            spool.EndDocPrinter(handle)
    finally:
        spool.ClosePrinter(handle)


def _spool_cups(printer_name, payload):
    args = ['lp', '-o', 'raw', '-t', 'pyPOS receipt']
    if printer_name:
        args.extend(['-d', printer_name])
    try:
        res = subprocess.run(args, input=payload, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=20, check=False)
    except FileNotFoundError as exc:
        raise PrinterError('CUPS lp command not installed. Install and configure CUPS for your TM-T81.') from exc
    except subprocess.TimeoutExpired as exc:
        raise PrinterError('Printer spooler timed out; check its queue before reprinting') from exc
    except OSError as exc:
        raise PrinterError(f'Cannot submit to the CUPS printer queue: {exc}') from exc
    if res.returncode:
        msg = res.stderr.decode('utf-8', 'replace').strip()[:300]
        raise PrinterError(f'CUPS print job rejected: {msg or res.returncode}')


def default_printer():
    if sys.platform == 'win32':
        return _windows_default_printer()
    try:
        res = subprocess.run(['lpstat', '-d'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=4, check=False)
        if res.returncode:
            return ''
        txt = res.stdout.decode('utf-8', 'replace').strip()
        return txt.split(':', 1)[1].strip() if ':' in txt else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''
