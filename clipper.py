"""
Автонарезка моментов из YouTube-ролика под TikTok (вертикальный 9:16)
с размытым фоном и автоматическими субтитрами.

Установка:
    pip install yt-dlp faster-whisper numpy deep-translator "av<16"
    + установленный ffmpeg

Запуск (проще через start.bat / start.py - там есть меню):
    python clipper.py "https://youtube.com/watch?v=..." --clips 5 --length 40
    python clipper.py "ссылка" --audio ru --subs ru
"""
import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import yt_dlp
from faster_whisper import WhisperModel

# Целый кадр по центру, сверху и снизу размытая копия
VF = (
    "split=2[a][b];"
    "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,scale=1080:1920[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)

SUB_COLORS = {"white": "&H00FFFFFF", "yellow": "&H0000E5FF", "pink": "&H007C5CFF", "cyan": "&H00D8E554"}

def ass_color(value: str) -> str:
    """Преобразует #RRGGBB в формат ASS (&HAABBGGRR)."""
    value = value.strip().lstrip("#")
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        return SUB_COLORS["white"]
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,84,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ---------- скачивание ----------

def probe(url: str):
    """Данные о ролике и его озвучках."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    found = {}
    for f in info.get("formats", []):
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue  # нужны только аудио-дорожки
        lang = f.get("language")
        if not lang:
            continue
        note = f.get("format_note") or ""
        name = note.split(",")[0].strip() or lang
        original = "original" in note.lower() or "default" in note.lower()
        old = found.get(lang)
        if old is None or (original and not old[2]):
            found[lang] = (lang, name, original)
    langs = list(found.values())
    langs.sort(key=lambda x: (not x[2], x[0] != "ru", x[1]))
    return {
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "langs": langs,
    }


def list_audio_languages(url: str):
    """[(код, название, это оригинал?), ...]"""
    return probe(url)["langs"]


def download(url: str, out: Path, audio: str = "") -> Path:
    for f in out.glob("source.*"):  # иначе yt-dlp решит, что ролик уже скачан
        f.unlink()
    if audio:
        fmt = f"bv*[height<=1080]+ba[language^={audio}]/bv*[height<=1080]+ba/b"
    else:
        fmt = "bv*[height<=1080]+ba/b"
    opts = {
        "format": fmt,
        "outtmpl": str(out / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return out / "source.mp4"


# ---------- звук и распознавание ----------

def extract_audio(video: Path, wav: Path, audio: str = "") -> None:
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    if audio.startswith("stream:"):
        stream_index = int(audio.split(":", 1)[1])
        cmd += ["-map", f"0:a:{stream_index}"]
    cmd += ["-ac", "1", "-ar", "16000", str(wav)]
    subprocess.run(cmd, check=True, capture_output=True)


def loudness_per_second(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    secs = len(data) // sr
    rms = np.sqrt((data[: secs * sr].reshape(secs, sr) ** 2).mean(axis=1))
    return rms / (rms.max() + 1e-9)


def _add_cuda_dlls():
    """Подключает библиотеки CUDA, если они поставлены через pip (install_gpu.bat)."""
    import os
    import site
    if not hasattr(os, "add_dll_directory"):
        return
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        for d in Path(base).glob("nvidia/*/bin"):
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


_add_cuda_dlls()


def _gpu_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _transcribe_on(wav: Path, model_size: str, device: str):
    model = WhisperModel(model_size, device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    segments, info = model.transcribe(
        str(wav), language="ru", vad_filter=True, word_timestamps=True,
        beam_size=1, condition_on_previous_text=False,
    )
    segs, words = [], []
    for s in segments:
        segs.append((s.start, s.end, s.text.strip()))
        for w in (s.words or []):
            words.append((w.start, w.end, w.word.strip()))
        print(f"\r  распознано до {s.end / 60:.1f} мин", end="", flush=True)
    print()
    return segs, words, info.language


def transcribe(wav: Path, model_size: str):
    if _gpu_available():
        try:
            print("  считаю на видеокарте")
            return _transcribe_on(wav, model_size, "cuda")
        except Exception as e:
            print(f"\n  видеокарта недоступна, считаю на процессоре ({str(e)[:80]})")
    else:
        print("  считаю на процессоре")
    return _transcribe_on(wav, model_size, "cpu")


# ---------- выбор моментов ----------

def norm(a: np.ndarray) -> np.ndarray:
    return a / (a.max() + 1e-9)


def pick_moments(loud, segs, clips: int, length: int, exclude=()):
    """Быстрый поиск: скользящие суммы + плотность речи + выразительность."""
    n = len(loud)
    if n < 2:
        return []
    words = np.zeros(n, dtype=np.float32)
    emph = np.zeros(n, dtype=np.float32)
    speech = np.zeros(n, dtype=np.float32)
    for s, e, text in segs:
        a, b = max(0, int(s)), min(int(e) + 1, n)
        if b <= a:
            continue
        words[a:b] += len(text.split()) / max(1, b-a)
        speech[a:b] = 1.0
        emph[a:b] += sum(text.count(ch) for ch in "!?…") + (0.35 if len(text.split()) >= 8 else 0)

    def moving_mean(arr, width):
        cs = np.concatenate(([0.0], np.cumsum(arr, dtype=np.float64)))
        starts = np.arange(0, max(1, n-width+1), 2)  # проверяем каждые 2 секунды
        ends = np.minimum(starts + width, n)
        return starts, (cs[ends] - cs[starts]) / np.maximum(1, ends-starts)

    width = min(max(10, int(length)), n)
    starts, loud_score = moving_mean(np.asarray(loud, dtype=np.float32), width)
    _, word_score = moving_mean(words, width)
    _, speech_score = moving_mean(speech, width)
    _, emph_score = moving_mean(emph, width)
    # Нормировка делает оценку устойчивее между роликами с разной громкостью.
    def scaled(a):
        peak = float(np.percentile(a, 95)) if len(a) else 0.0
        return np.clip(a / max(peak, 1e-6), 0, 1)
    score = (0.27*scaled(loud_score) + 0.33*scaled(word_score)
             + 0.25*speech_score + 0.15*scaled(emph_score))

    candidates = []
    for idx, st in enumerate(starts):
        en = min(int(st) + width, n)
        if any(st < xe + 7 and en > xs - 7 for xs, xe in exclude):
            continue
        candidates.append((float(score[idx]), int(st)))
    candidates.sort(key=lambda x: x[0], reverse=True)

    chosen = []
    min_gap = max(8, int(length * 0.82))
    for value, st in candidates:
        if all(abs(st - old) >= min_gap for old in chosen):
            chosen.append(st)
        if len(chosen) >= clips:
            break

    result = []
    for st in sorted(chosen):
        # Сдвигаем начало к ближайшей реплике, чтобы не обрезать слово.
        nearby = [(a, b) for a, b, _ in segs if b >= st-5 and a <= st+8]
        begin = max(0.0, min((a for a, _ in nearby), default=float(st)))
        finish = min(float(n), begin + length)
        if finish - begin < min(10, length):
            begin = float(st)
            finish = min(float(n), begin + length)
        result.append((begin, finish))
    return result


# ---------- субтитры ----------

def ts(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def chunks_from_words(words, start: float, end: float):
    """Субтитры на языке озвучки: по 3-4 слова по меткам времени слов."""
    groups, cur = [], []
    for w in words:
        if w[1] <= start or w[0] >= end:
            continue
        if cur and (len(cur) >= 4 or w[0] - cur[-1][1] > 0.7):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    result = []
    for c in groups:
        a = c[0][0] - start
        b = min(c[-1][1], end) - start
        result.append((a, b, " ".join(x[2] for x in c)))
    return result


def translate_texts(texts, target: str):
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print("\nДля перевода нужна библиотека: python -m pip install deep-translator")
        print("Пока субтитры будут без перевода.")
        return texts
    try:
        tr = GoogleTranslator(source="auto", target=target)
        out = []
        for i in range(0, len(texts), 20):
            part = texts[i:i + 20]
            res = tr.translate_batch(part)
            out += [r or t for r, t in zip(res, part)]
        return out
    except Exception as e:
        print(f"\nНе удалось перевести субтитры ({e}). Они будут без перевода.")
        return texts


def chunks_translated(segs, start: float, end: float, target: str):
    """Субтитры с переводом: переводим целые фразы, потом режем на куски по 4 слова."""
    part = [(s, e, t) for s, e, t in segs if e > start and s < end and t.strip()]
    texts = translate_texts([t for _, _, t in part], target)
    result = []
    for (s, e, _), tt in zip(part, texts):
        s2, e2 = max(s, start), min(e, end)
        ws = tt.split()
        if not ws or e2 <= s2:
            continue
        groups = [ws[i:i + 4] for i in range(0, len(ws), 4)]
        step = (e2 - s2) / len(groups)
        for k, g in enumerate(groups):
            result.append((s2 + k * step - start, s2 + (k + 1) * step - start, " ".join(g)))
    return result


def write_ass(chunks, path: Path, color: str = "white", font_size: int = 84) -> None:
    lines = []
    for a, b, text in chunks:
        b = max(b, a + 0.3)
        text = text.upper().replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ts(a)},{ts(b)},Default,,0,0,0,,{text}")
    header = ASS_HEADER.replace("&H00FFFFFF,", (SUB_COLORS.get(color, ass_color(color))) + ",", 1)
    header = header.replace("Default,Arial,84,", f"Default,Arial,{max(36, min(int(font_size), 120))},")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- нарезка ----------

def media_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def insert_ad(clip_file: Path, ad_file: Path) -> None:
    """Вставляет рекламу в точную середину готового клипа, не накладывая её поверх."""
    if not ad_file.is_file():
        raise FileNotFoundError(f"Не найден рекламный файл: {ad_file}")
    duration = media_duration(clip_file)
    ad_duration = media_duration(ad_file)
    midpoint = duration / 2.0
    temp_file = clip_file.with_name(clip_file.stem + "_with_ad.mp4")

    # Реклама сохраняется целиком по ширине, а свободное место заполняется
    # размытым фоном. Для исходного горизонтального ролика это выглядит аккуратно.
    fc = (
        f"[0:v]trim=start=0:duration={midpoint:.6f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p[v0];"
        f"[0:v]trim=start={midpoint:.6f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p[v2];"
        "[1:v]split=2[ab][af];"
        "[ab]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,scale=1080:1920[bg];"
        "[af]scale=1080:-2,colorkey=0x00FF00:0.22:0.08[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,setsar=1,format=yuv420p[va];"
        f"[0:a]asplit=2[a0][a2];[a0]atrim=start=0:duration={midpoint:.6f},asetpts=PTS-STARTPTS,aresample=48000[a0o];"
        f"[a2]atrim=start={midpoint:.6f},asetpts=PTS-STARTPTS,aresample=48000[a2o];"
        "[1:a]asetpts=PTS-STARTPTS,aresample=48000[aa];"
        "[v0][a0o][va][aa][v2][a2o]concat=n=3:v=1:a=1[v][a]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(clip_file), "-i", str(ad_file),
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temp_file),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        temp_file.replace(clip_file)
        print(f"  Реклама вставлена в середину (+{ad_duration:.1f} сек.)")
    except subprocess.CalledProcessError as e:
        temp_file.unlink(missing_ok=True)
        print(e.stderr.decode(errors="ignore")[-2000:])
        raise


def cut(video: Path, start: float, end: float, out_file: Path, ass_name, workdir: Path, ad_file: Path | None = None) -> None:
    vf = VF + (f",ass={ass_name}" if ass_name else "")
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(video),
        "-t", f"{end - start:.2f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-threads", "0",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_file),
    ]
    try:
        # cwd = папка с клипами, чтобы путь к .ass был коротким (без проблем с C:\)
        subprocess.run(cmd, check=True, capture_output=True, cwd=str(workdir))
        if ad_file is not None:
            insert_ad(out_file, ad_file)
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode(errors="ignore")[-1500:])
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--clips", type=int, default=5, help="сколько клипов вырезать")
    p.add_argument("--length", type=int, default=40, help="длина клипа, сек")
    p.add_argument("--model", default="small", help="модель Whisper: tiny/base/small/medium")
    p.add_argument("--audio", default="", help="код языка озвучки (ru, en, ...); пусто - оригинал")
    p.add_argument("--subs", default="orig",
                   help="субтитры: orig - как в озвучке, none - без них, либо код языка для перевода (ru, en, ...)")
    p.add_argument("--style", default="white", help="цвет субтитров: white / yellow / pink или #RRGGBB")
    p.add_argument("--font-size", type=int, default=84, help="размер субтитров")
    p.add_argument("--ad", default="", help="видеофайл рекламы для вставки в середину каждого клипа")
    p.add_argument("--out", default="clips")
    p.add_argument("--reset", action="store_true", help="забыть уже вырезанные моменты и начать заново")
    args = p.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(exist_ok=True)

    video = out / "source.mp4"
    wav = out / "audio.wav"
    tj = out / "transcript.json"
    meta_file = out / "meta.json"
    used_file = out / "used.json"
    if args.reset:
        used_file.unlink(missing_ok=True)

    local_source = Path(args.url).expanduser()
    is_local_source = local_source.is_file()
    meta = {"url": str(local_source.resolve()) if is_local_source else args.url, "audio": args.audio}
    old = None
    if meta_file.exists():
        old = json.loads(meta_file.read_text(encoding="utf-8"))
    elif (out / "url.txt").exists():  # папка от прежней версии
        old = {"url": (out / "url.txt").read_text(encoding="utf-8").strip(), "audio": ""}
    same = (old == meta) or (old is None and not args.audio)

    if is_local_source:
        if not video.exists() or not same:
            import shutil
            print("Копирую загруженное видео в проект...")
            shutil.copy2(local_source, video)
            wav.unlink(missing_ok=True)
            tj.unlink(missing_ok=True)
            used_file.unlink(missing_ok=True)
        else:
            print("Ролик уже загружен, пропускаю копирование.")
    elif same and video.exists():
        print("Ролик уже скачан, пропускаю.")
    else:
        print("Скачиваю ролик...")
        video = download(args.url, out, args.audio)
        wav.unlink(missing_ok=True)
        tj.unlink(missing_ok=True)
        used_file.unlink(missing_ok=True)
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    if not wav.exists():
        extract_audio(video, wav, args.audio)

    if tj.exists():
        print("Беру сохранённое распознавание речи.")
        data = json.loads(tj.read_text(encoding="utf-8"))
        segs, words, lang = data["segs"], data["words"], data.get("lang", "")
    else:
        print("Распознаю речь...")
        segs, words, lang = transcribe(wav, args.model)
        tj.write_text(
            json.dumps({"segs": segs, "words": words, "lang": lang}, ensure_ascii=False),
            encoding="utf-8",
        )

    loud = loudness_per_second(wav)
    used = json.loads(used_file.read_text(encoding="utf-8")) if used_file.exists() else []

    print("Ищу лучшие моменты...")
    moments = pick_moments(loud, segs, args.clips, args.length, used)
    if not moments:
        print("Подходящих моментов в этом ролике больше не осталось.")
        return

    for i, (s, e) in enumerate(moments, len(used) + 1):
        if args.subs == "none":
            ass_name = None
        else:
            ass_name = f"clip_{i}.ass"
            if args.subs == "orig" or args.subs == lang:
                chunks = chunks_from_words(words, s, e)
            else:
                chunks = chunks_translated(segs, s, e, args.subs)
            write_ass(chunks, out / ass_name, args.style, args.font_size)
        print(f"Клип {i}: {s:.0f}с - {e:.0f}с")
        ad_file = Path(args.ad).resolve() if args.ad else None
        cut(video, s, e, out / f"clip_{i}.mp4", ass_name, out, ad_file)

    used += [[s, e] for s, e in moments]
    used_file.write_text(json.dumps(used), encoding="utf-8")

    print(f"Готово! Клипы в папке: {out}")


if __name__ == "__main__":
    main()
