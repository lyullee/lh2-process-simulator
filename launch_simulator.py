"""Windows launcher. --self-check never starts HTTP, a browser, or a scenario."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser

APP_VERSION = '0.1.0'
PRODUCT_NAME = '가상 LH2 공정 시뮬레이터'
BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(BASE/'src'))


def self_check():
    import tkinter
    import sqlite3
    from authoring_ui.server import build_bootstrap, SCENARIO_MODULES, STATIC, DESIGN_DIR
    from lh2dt.properties import HydrogenProperties
    bootstrap = build_bootstrap()
    missing = [str(STATIC / name) for name in ('index.html','app.js','styles.css','monitor.js','units.js','parameter_labels.js') if not (STATIC/name).is_file()]
    thermo = HydrogenProperties().from_pT(101325, 300)
    tcl_version = tkinter.Tcl().eval('info patchlevel')
    report = {'product': PRODUCT_NAME, 'version': APP_VERSION, 'passed': not missing,
              'node_count':len(bootstrap['design']['nodes']), 'line_count':len(bootstrap['design']['edges']),
              'templates':len(bootstrap['library']['templates']), 'scenarios':len(SCENARIO_MODULES),
              'property_backend_loaded':thermo.density_kg_m3>0, 'missing':missing,
              'tk_runtime':tcl_version, 'sqlite_runtime':sqlite3.sqlite_version,
              'design_directory':str(DESIGN_DIR), 'http_server_started':False,
              'scenario_run_started':False, 'frozen':bool(getattr(sys,'frozen',False))}
    if missing: raise RuntimeError(json.dumps(report,ensure_ascii=False))
    return report


def graphical_launcher(port):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from http.server import ThreadingHTTPServer
    from authoring_ui.server import AuthoringHandler, JOBS, JOBS_LOCK

    window = tk.Tk()
    window.title(PRODUCT_NAME)
    window.geometry('570x310')
    window.minsize(520, 290)
    container=ttk.Frame(window,padding=22)
    container.pack(fill='both',expand=True)
    ttk.Label(container,text=PRODUCT_NAME,font=('맑은 고딕',16,'bold')).pack(anchor='w')
    ttk.Label(container,text=f'v{APP_VERSION} · 공정 저작·센서 관측·동적 시뮬레이션',padding=(0,8)).pack(anchor='w')
    status=tk.StringVar(value='시작 준비 중')
    ttk.Label(container,textvariable=status,wraplength=510).pack(anchor='w',pady=12)
    ttk.Label(container,text='이 창을 닫거나 「종료」를 누르면 직접 시작한 서버와 계산이 종료됩니다.\n다른 프로그램이 사용 중인 서버는 종료하지 않습니다.',wraplength=510).pack(anchor='w',pady=8)
    server=None
    reused=False
    url=f'http://127.0.0.1:{port}'

    def shutdown():
        nonlocal server
        if server is not None:
            with JOBS_LOCK:
                for job in JOBS.values():
                    job.cancelled.set();job.gate.set()
            server.shutdown();server.server_close();server=None
        window.destroy()

    def start():
        nonlocal server,reused
        try:
            server=ThreadingHTTPServer(('127.0.0.1',port),AuthoringHandler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            status.set(f'실행 중 · {url}\n화면에서 Run을 누르기 전에는 시나리오를 실행하지 않습니다.')
            webbrowser.open(url)
        except OSError as error:
            status.set(f'포트 {port}를 사용할 수 없습니다. 기존 프로그램을 종료하거나 다른 포트를 지정하세요.')
            messagebox.showerror(PRODUCT_NAME,str(error),parent=window)

    actions=ttk.Frame(container)
    actions.pack(fill='x',pady=12)
    ttk.Button(actions,text='시뮬레이터 화면 열기',command=lambda:webbrowser.open(url)).pack(side='left',padx=(0,10))
    ttk.Button(actions,text='종료 (서버·계산 중지)',command=shutdown).pack(side='right')
    window.protocol('WM_DELETE_WINDOW',shutdown)
    window.after(200,start)
    window.mainloop()


def main():
    parser=argparse.ArgumentParser(description=PRODUCT_NAME)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--console',action='store_true',help='브라우저를 자동으로 열지 않는 서버 실행')
    args=parser.parse_args()
    if not 1<=args.port<=65535: parser.error('포트는 1~65535입니다')
    try:
        if args.self_check:
            result=self_check()
            content=json.dumps(result,ensure_ascii=False,indent=2)
            if args.report:
                args.report.parent.mkdir(parents=True,exist_ok=True)
                args.report.write_text(content+'\n',encoding='utf-8')
            if sys.stdout is not None: print(content)
            return 0
        if args.console:
            from authoring_ui.server import serve
            serve('127.0.0.1',args.port)
        else:
            graphical_launcher(args.port)
        return 0
    except Exception as error:
        result={'passed':False,'error':str(error),'http_server_started':False if args.self_check else None}
        if args.report: args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if sys.stderr is not None: print(str(error),file=sys.stderr)
        if not args.self_check:
            from tkinter import messagebox
            messagebox.showerror(PRODUCT_NAME,str(error))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
