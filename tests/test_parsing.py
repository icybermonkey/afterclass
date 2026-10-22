from afterclass.steps import analyze, ocr, scenes, silence, transcribe


def test_silence_parse_open_end():
    log = ("[silencedetect @ 0x1] silence_start: 0\n"
           "[silencedetect @ 0x1] silence_end: 896.8 | silence_duration: 896.8\n"
           "[silencedetect @ 0x1] silence_start: 5688.3\n")
    assert silence.parse(log, 6000) == [[0.0, 896.8], [5688.3, 6000.0]]


def test_scene_csv_with_timecode_row(tmp_path):
    p = tmp_path / "scenes.csv"
    p.write_text("Timecode List:,00:00:10.000\n"
                 "Scene Number,Start Frame,Start Timecode,Start Time (seconds),End Frame,End Timecode,End Time (seconds)\n"
                 "1,1,00:00:00.000,0.000,250,00:00:10.000,10.000\n"
                 "2,251,00:00:10.000,10.000,500,00:00:20.000,20.000\n")
    assert scenes.parse_csv(p) == [(1, 0.0, 10.0), (2, 10.0, 20.0)]


def seg(start, text, nsp=0.0):
    return {"start": start, "end": start + 2, "text": text, "no_speech_prob": nsp}


def test_clean_drops_silence_fillers_and_loops():
    raw = [seg(0, "Thank you.", 0.9),          # filler in noise
           seg(10, "Static analysis."),
           seg(100, "Hello there")]             # inside silence
    raw += [seg(20 + i * 2, "Okay.") for i in range(12)]  # a loop
    out, rep = transcribe.clean(raw, silences=[[95, 110]])
    texts = [s["text"] for s in out]
    assert "Static analysis." in texts
    assert "Hello there" not in texts
    assert texts.count("Okay.") == 2
    assert rep["loops"] == 1
    assert rep["dropped"]["silence"] == 1


def test_srt_format():
    srt = transcribe.to_srt([{"start": 3661.5, "end": 3662.25, "text": " Hi "}])
    assert srt.startswith("1\n01:01:01,500 --> 01:01:02,250\nHi\n")


def _tsv(words, W=1920, H=1080):
    """Build tesseract TSV: words = [(text, left, top, h, conf, line_no)]."""
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [head, f"1\t1\t0\t0\t0\t0\t0\t0\t{W}\t{H}\t-1\t"]
    for i, (text, left, top, h, conf, line) in enumerate(words):
        rows.append(f"5\t1\t{line}\t1\t1\t{i}\t{left}\t{top}\t50\t{h}\t{conf}\t{text}")
    return "\n".join(rows)


def test_ocr_title_and_page():
    a = ocr.analyze(ocr.parse_tsv(_tsv([
        ("Page", 10, 49, 15, 96, 1), ("33", 60, 49, 15, 96, 1), ("of", 90, 49, 15, 96, 1), ("124", 120, 49, 15, 96, 1),
        ("Static", 10, 94, 45, 96, 2), ("Analysis", 200, 94, 45, 96, 2),
        ("analysis", 10, 268, 30, 96, 3), ("without", 200, 268, 30, 96, 3), ("executing", 400, 268, 30, 96, 3),
    ])))
    assert (a["page"], a["pages"]) == (33, 124)
    assert a["title"] == "Static Analysis"
    assert not a["demo"] and not a["divider"]


def test_ocr_demo_menu_bar():
    a = ocr.analyze(ocr.parse_tsv(_tsv([
        ("Sublime", 10, 2, 9, 0, 1), ("File", 100, 2, 9, 9, 1), ("Edit", 140, 2, 9, 9, 1), ("View", 180, 2, 9, 9, 1),
        ("def", 10, 300, 20, 90, 2), ("main():", 60, 300, 20, 90, 2),
    ])))
    assert a["demo"]


def test_ocr_divider_slide():
    a = ocr.analyze(ocr.parse_tsv(_tsv([
        ("Code", 10, 430, 59, 96, 1), ("Obfuscation", 200, 430, 59, 96, 1), ("Techniques", 500, 430, 75, 95, 1),
        ("Opaque", 10, 605, 49, 95, 2), ("Predicates", 200, 602, 42, 96, 2), ("Ly", 300, 667, 15, 6, 3),
    ])))
    assert a["divider"] == ["Code Obfuscation Techniques", "Opaque Predicates"]
    single = ocr.analyze(ocr.parse_tsv(_tsv([("DISPATCH", 10, 467, 24, 96, 1)])))
    assert single["divider"] == []  # one random word is not a section


def test_divider_titles_strip_deck_title():
    lists = [["Code Obfuscation Techniques", "Opaque Predicates"],
             ["Code Obfuscation Techniques", "Opaque Predicates"],
             ["Code Obfuscation Techniques", "Virtual Machines"],
             ["Conclusion"]]
    assert analyze.divider_titles(lists) == ["Opaque Predicates", "Opaque Predicates", "Virtual Machines", "Conclusion"]
