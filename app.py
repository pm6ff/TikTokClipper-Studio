import json, os, re, sys, threading, time, uuid, shutil, subprocess, mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import webbrowser

HERE=Path(__file__).resolve().parent
os.chdir(HERE)
PROJECTS=HERE/"projects"
UPLOADS=HERE/"uploads"
HISTORY_FILE=HERE/"history.json"
for p in (PROJECTS,UPLOADS): p.mkdir(exist_ok=True)
LOCK=threading.RLock()
QUEUE=[]
CURRENT=None
WORKER=None
ACTIVE_PROC=None
CANCEL=False
HISTORY=[]
try:
    HISTORY=json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
except Exception: HISTORY=[]

def save_history():
    with LOCK: HISTORY_FILE.write_text(json.dumps(HISTORY,ensure_ascii=False,indent=2),encoding="utf-8")

def snapshot():
    with LOCK:
        return {"running": bool(CURRENT), "current": CURRENT.copy() if CURRENT else None,
                "queue":[x.copy() for x in QUEUE], "history":HISTORY[:50]}

def update_current(**kw):
    with LOCK:
        if CURRENT: CURRENT.update(kw)

def worker_loop():
    global CURRENT,CANCEL,ACTIVE_PROC
    while True:
        with LOCK:
            if not QUEUE: CURRENT=None; return
            job=QUEUE.pop(0); CURRENT=job; CANCEL=False
        jobid=job["id"]; out=HERE/job.get("project", f"projects/{jobid}"); out.mkdir(exist_ok=True,parents=True)
        cfg=job["cfg"]; source=job["source"]
        args=[sys.executable,str(HERE/"clipper.py"),source,"--clips",str(cfg["clips"]),
              "--length",str(cfg["length"]),"--subs",cfg["subs"],"--model",cfg["model"],
              "--style",cfg["style"],"--font-size",str(cfg["font_size"]),"--audio",cfg.get("audio",""),"--out",str(out)]
        if cfg.get("insert_ad"): args += ["--ad",str(HERE/"assets"/"advertisement.mp4")]
        if job.get("reset_used"): args += ["--reset"]
        update_current(status="В очереди",progress=0,detail="Подготовка…")
        started=time.time(); proc=None
        try:
            duration=0
            if source.startswith(("http://", "https://")):
                import clipper
                info=clipper.probe(source)
                langs=info.get("langs",[])
                duration=float(info.get("duration") or 0)
                selected_audio = cfg.get("audio", "")
                if selected_audio and not any(code == selected_audio for code, _, _ in langs):
                    raise ValueError("Выбранная аудиодорожка больше недоступна. Обнови список дорожек.")
            elif Path(source).is_file():
                probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",source],capture_output=True,text=True)
                if probe.returncode==0:
                    try: duration=float(probe.stdout.strip())
                    except ValueError: pass
            popen_kw={"cwd":str(HERE),"stdout":subprocess.PIPE,"stderr":subprocess.STDOUT,
                      "text":True,"encoding":"utf-8","errors":"replace","bufsize":1}
            if os.name=="nt":
                popen_kw["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kw["start_new_session"]=True
            proc=subprocess.Popen(args,**popen_kw)
            ACTIVE_PROC=proc
            update_current(status="Обработка",detail="Запускаю обработку…")
            clip_names=[]
            buffer=""
            while True:
                ch=proc.stdout.read(1)
                if ch=="":
                    if buffer.strip():
                        line=buffer.strip()
                    else:
                        break
                elif ch not in ("\r","\n"):
                    buffer+=ch
                    continue
                else:
                    line=buffer.strip(); buffer=""
                    if not line: continue
                if CANCEL:
                    if os.name=="nt":
                        subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],
                                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    else:
                        import signal
                        try: os.killpg(proc.pid,signal.SIGTERM)
                        except ProcessLookupError: pass
                    break
                progress=8; stage="Обработка"
                if line.startswith("Скачиваю"): progress=8; stage="Скачивание"
                elif "Копирую загруженное" in line: progress=12; stage="Подготовка файла"
                elif "Распознаю" in line: progress=18; stage="Распознавание"
                elif "распознано до" in line:
                    stage="Распознавание"
                    mm=re.search(r"распознано до ([\d.]+) мин",line)
                    mins=float(mm.group(1)) if mm else 0
                    progress=min(62,20+int(42*mins*60/duration)) if duration else 35
                elif "сохранённое распознавание" in line: progress=62; stage="Распознавание"
                elif line.startswith("Ищу лучшие"): progress=68; stage="Поиск моментов"
                elif re.match(r"Клип \d+:",line):
                    stage="Рендер клипов"
                    m=re.match(r"Клип (\d+):",line)
                    clip_index=int(m.group(1)) if m else 1
                    progress=min(96,70+int(25*min(clip_index,cfg["clips"])/max(1,cfg["clips"])))
                    clip_names=[p.name for p in out.glob("clip_*.mp4")]
                update_current(status=stage,progress=progress,detail=line)
                if ch=="": break
            code=proc.wait()
            ACTIVE_PROC=None
            if CANCEL:
                update_current(status="Отменено",detail="Обработка остановлена пользователем.",progress=0)
                status="cancelled"; err=""
            elif code==0:
                clip_names=sorted([p.name for p in out.glob("clip_*.mp4")],key=lambda n:int(re.search(r'\d+',n).group()))
                update_current(status="Готово",progress=100,detail="Клипы готовы.")
                status="done"; err=""
            else:
                status="error"; err=f"Процесс завершился с кодом {code}"
                update_current(status="Ошибка",detail=err)
        except Exception as e:
            ACTIVE_PROC=None
            status="error"; err=str(e); clip_names=[]
            update_current(status="Ошибка",detail=err)
        rec={"id":jobid,"title":job["title"],"source":source,"project":str(out.relative_to(HERE)),
             "created":time.strftime("%Y-%m-%d %H:%M"),"status":status,"clips":clip_names if status=="done" else [],
             "settings":cfg,"error":err,"elapsed":round(time.time()-started),"source":source}
        with LOCK:
            HISTORY.insert(0,rec); del HISTORY[100:]
        save_history()
        update_current(history_id=jobid)
        time.sleep(.2)
        with LOCK: CURRENT=None
        if status=="cancelled":
            # Leave queued jobs intact; cancellation only applies to the active job.
            pass

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_json(self,obj,code=200):
        b=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def send_file(self,path,ctype):
        path=Path(path)
        if not path.is_file(): return self.send_error(404)
        size=path.stat().st_size; start=0; end=size-1; code=200
        m=re.match(r"bytes=(\d*)-(\d*)",self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            a,b=m.groups()
            if a=="": start=max(size-int(b),0)
            else: start=int(a); end=min(int(b),size-1) if b else size-1
            code=206
        self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Accept-Ranges","bytes")
        self.send_header("Content-Length",str(end-start+1))
        if code==206: self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path,"rb") as f:
                f.seek(start); left=end-start+1
                while left:
                    chunk=f.read(min(65536,left))
                    if not chunk: break
                    self.wfile.write(chunk); left-=len(chunk)
        except (BrokenPipeError,ConnectionError): pass
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=="/": return self.send_file(HERE/"ui.html","text/html; charset=utf-8")
        if u.path=="/api/status": return self.send_json(snapshot())
        if u.path=="/assets/advertisement.mp4": return self.send_file(HERE/"assets"/"advertisement.mp4","video/mp4")
        m=re.fullmatch(r"/upload-preview/([A-Za-z0-9._-]+)",u.path)
        if m:
            candidate=UPLOADS/m.group(1)
            ext=candidate.suffix.lower()
            ctype={".mp4":"video/mp4",".mov":"video/quicktime",".mkv":"video/x-matroska",".webm":"video/webm"}.get(ext,"application/octet-stream")
            return self.send_file(candidate,ctype)
        if u.path=="/api/audio":
            import clipper
            url=parse_qs(u.query).get("url",[""])[0]
            try:
                if url.startswith(("http://","https://")):
                    return self.send_json(clipper.probe(url))
                path=Path(url)
                if path.is_file():
                    cmd=["ffprobe","-v","error","-select_streams","a","-show_entries",
                         "stream=index:stream_tags=language,title","-of","json",str(path)]
                    result=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
                    if result.returncode: raise RuntimeError(result.stderr[-500:])
                    streams=json.loads(result.stdout or "{}").get("streams",[])
                    langs=[]
                    for i,stream in enumerate(streams):
                        tags=stream.get("tags",{})
                        code=tags.get("language") or f"stream:{i}"
                        name=tags.get("title") or (f"Дорожка {i+1} · {code}" if not code.startswith("stream:") else f"Дорожка {i+1} · оригинал")
                        langs.append((code,name,i==0))
                    if not langs: langs=[("", "Оригинальная аудиодорожка", True)]
                    return self.send_json({"title":path.name,"langs":langs,"duration":0})
                return self.send_json({"langs":[],"error":"Сначала добавь видеофайл или ссылку."})
            except Exception as e: return self.send_json({"langs":[],"error":str(e)})
        m=re.fullmatch(r"/media/([a-f0-9]{12})/(clip_\d+\.mp4)",u.path)
        if m:
            rec=next((x for x in HISTORY if x["id"]==m.group(1)),None)
            if rec: return self.send_file(HERE/rec["project"]/m.group(2),"video/mp4")
        m=re.fullmatch(r"/media/([a-f0-9]{12})/source",u.path)
        if m:
            rec=next((x for x in HISTORY if x["id"]==m.group(1)),None)
            if rec: return self.send_file(HERE/rec["project"]/"source.mp4","video/mp4")
        self.send_error(404)
    def do_POST(self):
        global WORKER, CANCEL, ACTIVE_PROC
        n=int(self.headers.get("Content-Length") or 0)
        raw=self.rfile.read(n) if n else b"{}"
        if self.path=="/api/upload":
            name=self.headers.get("X-Filename","video.mp4")
            safe=re.sub(r"[^A-Za-z0-9._-]+","_",Path(name).name)[:100] or "video.mp4"
            if not safe.lower().endswith((".mp4",".mov",".mkv",".webm")): return self.send_json({"error":"Поддерживаются MP4, MOV, MKV и WEBM."},400)
            target=UPLOADS/(uuid.uuid4().hex[:12]+"_"+safe)
            target.write_bytes(raw)
            return self.send_json({"ok":True,"source":str(target),"name":safe})
        try: data=json.loads(raw or b"{}")
        except: data={}
        if self.path=="/api/start":
            if CURRENT or WORKER and WORKER.is_alive() and QUEUE:
                pass
            source=str(data.get("source") or data.get("url") or "").strip()
            local=Path(source)
            if not (source.startswith("https://") or source.startswith("http://") or local.is_file()):
                return self.send_json({"error":"Добавь ссылку YouTube или загрузи видеофайл."},400)
            try:
                cfg={"clips":max(1,min(20,int(data.get("clips",3)))),
                     "length":max(10,min(120,int(data.get("length",40)))),
                     "model":data.get("model","base") if data.get("model") in ("tiny","base","small") else "base",
                     "subs":data.get("subs","ru"),"style":data.get("style","#FFFFFF"),
                     "font_size":max(42,min(112,int(data.get("font_size",84)))),
                     "insert_ad":bool(data.get("insert_ad",True)),
                     "audio":str(data.get("audio","") or "")}
                import clipper
                if cfg["style"] not in clipper.SUB_COLORS and not re.fullmatch(r"#[0-9A-Fa-f]{6}",cfg["style"]):
                    return self.send_json({"error":"Неверный цвет субтитров."},400)
            except Exception as e: return self.send_json({"error":str(e)},400)
            title=data.get("title") or (Path(source).name if local.is_file() else source)
            jid=uuid.uuid4().hex[:12]
            job={"id":jid,"title":title,"source":source,"cfg":cfg,"status":"В очереди","progress":0,
                 "detail":"Ожидает запуска","project":data.get("project") or f"projects/{jid}","reset_used":bool(data.get("reset_used"))}
            with LOCK:
                QUEUE.append(job)
                if not WORKER or not WORKER.is_alive():
                    WORKER=threading.Thread(target=worker_loop,daemon=True); WORKER.start()
            return self.send_json({"ok":True,"id":jid,"queued":True})
        if self.path=="/api/cancel":
            with LOCK:
                if CURRENT:
                    CANCEL=True
                    proc=ACTIVE_PROC
                else:
                    return self.send_json({"error":"Сейчас нет активной обработки."},409)
            if proc and proc.poll() is None:
                if os.name=="nt":
                    subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:
                    import signal
                    try: os.killpg(proc.pid,signal.SIGTERM)
                    except ProcessLookupError: pass
            return self.send_json({"ok":True})
        if self.path=="/api/open":
            try: os.startfile(PROJECTS)
            except Exception: pass
            return self.send_json({"ok":True})
        self.send_error(404)

def main():
    server=None
    for port in range(8765,8785):
        try: server=ThreadingHTTPServer(("127.0.0.1",port),H); break
        except OSError: continue
    if not server: print("Не удалось найти свободный порт."); return
    url=f"http://127.0.0.1:{port}"
    print(f"Clipper Studio запущен: {url}")
    threading.Timer(.8,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
if __name__=="__main__": main()
