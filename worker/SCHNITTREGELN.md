# Velox Cut – Schnittregeln

Diese Datei ist das „Gedächtnis“ der Schnitt-Pipeline (`worker/pipeline.py`). Jede Rückmeldung zu einem geschnittenen
Video wird hier als feste Regel festgehalten und im Code umgesetzt – so wird jeder Schnitt besser als der vorige.
Protokoll aller Schnitte: `schnitt-maschine/logs/jobs.jsonl` (neben dem Ordner `app`) (was entfernt wurde, Probleme, Gemini an/aus).

## Grundregeln (alle Stile)
1. Geschnitten wird nur **zwischen Wörtern** – nie mitten im Wort oder Satz.
2. Raus: Füllwörter (äh, ähm, öhm, hm …), Pausen über ~0,2 s (auf 0,05–0,08 s Luft gekürzt), gedehnte Stille in Wörtern,
   direkt wiederholte Satzteile („ich hab ich habe“ → erste Fassung raus), „Genau.“/„Okay.“ als Mini-Satz am Anfang/Ende.
3. Mit Gemini zusätzlich: Versprecher, abgebrochene Satzanfänge, Selbstkorrekturen. Im Zweifel behalten.
4. Clips aus langen Videos: 20–60 s, Start mit starkem Hook-Satz, Ende erst wenn der Gedanke fertig ist.
5. Bild: 9:16 aufs Gesicht, Gesicht eher im oberen Drittel; dunkle Videos werden aufgehellt.
6. Untertitel: Stil + Farbe wie in der Vorschau, ganze Phrase mittig, gesprochenes Wort in Akzentfarbe,
   weichen automatisch dem Gesicht aus (darunter, sonst darüber).
7. **Stille wird je Video gemessen** (Lautstärke in 50-ms-Schritten, Schwelle knapp über dem Grundrauschen) –
   feste Schwellen scheitern an Raum-/Mikrofonrauschen. Lücken zwischen Wörtern werden NUR geschnitten, wo wirklich
   Stille ist (Whisper-Zeitstempel verrutschen, in „Lücken“ steckt oft noch Sprache).
8. Jedes Wort behält mindestens seine normale Länge; Satzenden dürfen ausklingen (+0,08 s).
9. Füllwörter werden bis zur umgebenden Pause bzw. bis zur Lautstärke-Senke geschnitten (Whisper setzt sie oft zu kurz/zu früh an).
10. Verdächtig lange Wörter („Restaurants“ 1,4 s, „uuund“) werden mit Kontext neu abgehört: verstecktes „ähm“ raus,
    sonst Anfang/Mitte/Ende kürzen – die Variante, bei der das Wort danach am klarsten erkannt wird, gewinnt.
11. **Hörprobe**: fertigen Clip neu transkribieren und mit dem Text vergleichen. Fehlen Wörter → automatisch im sicheren
    Modus neu schneiden. Gehörte Füllwörter werden protokolliert.
12. **Story:** Das Video beginnt erst mit der eigentlichen Szene (wenn die Person die andere anspricht bzw. wirklich
    zur Kamera spricht). Vorbereitung, Absprachen mit dem Kameramann, Fehlversuche raus – bei „nochmal von vorne“ gilt
    nur der letzte Versuch. Es endet nach dem Abschluss/Abschied; alles danach („okay, super, passt?“) fliegt raus.
    KI entscheidet (Gemini, mit Wiederholungen bei Überlastung), sonst Regeln (Neustart-Wörter, Abschiedswörter).
13. **Flüssig statt abgehackt:** kurze Pausen bleiben natürlich stehen, an geschnittenen Pausen bleiben 0,1 s Luft
    je Seite (Viral 0,06 s); Schnitte bildgenau; Ton wird an jedem Schnitt 25 ms überblendet; Abschnitte unter 0,3 s werden
    mit dem Nachbarn verbunden. Bildausschnitt bei Clean fest (Gruppen/Totalen nie Zoom-Sprünge), Zoom-Wechsel nur bei
    einer Person in Nahaufnahme.
14. **Untertitel exakt synchron:** Zeiten kommen aus dem FERTIGEN Ton (zweite Abhörung nach dem Schnitt), Text aus dem
    Original. Bei Clean ruhig (nur Farbe, kein Springen/Wackeln), Pop-Animation nur bei Viral. Position weicht allen
    erkannten Gesichtern aus.
15. **Qualitäts-Check** vor Auslieferung: Untertitel vorhanden, Pausen/Füller wirklich entfernt, Datei 1080×1920 mit Ton,
   Länge stimmt. Sonst: Auftrag fehlgeschlagen → Credits automatisch zurück.
16. **Cutter-Denkweise (Rohmaterial):** Kunden laden komplette Rohdateien hoch. Die KI entscheidet **Satz für Satz**, ob er ins
    fertige Video gehört (Test: „Würde ein Zuschauer ihn vermissen?“) und schreibt zu jedem entfernten Satz den Grund ins
    Protokoll. Der Zuschauer darf nie merken, dass gedreht, abgesprochen oder wiederholt wurde.
17. **Wiederholte Anläufe ohne „von vorne“:** Wer sich verspricht und den Satz einfach nochmal sagt → nur die letzte vollständige
    Fassung bleibt – auch ohne KI (Regel erkennt abgebrochene/fast gleiche Sätze und Neustarts im Satz). Die letzte Fassung ist
    geschützt: sie darf von niemandem entfernt werden. Gleiche Frage an verschiedene Personen (dazwischen echter Inhalt) bleibt.
18. **Verschluckte Wörter nachhören:** Die Spracherkennung schreibt wiederholte Anläufe oft nur einmal auf (der zweite steckt dann
    in einem 3 s langen „und“ oder in einer „Lücke“ voller Sprache). Solche Stellen werden einzeln neu abgehört und die Wörter
    eingesetzt, bevor geschnitten wird.
19. **Sätze an den Kameramann** („passt?“, „läuft?“, „bist du so weit?“, „nochmal bitte“, kurze „sorry“/„warte“) fliegen
    auch mitten im Video raus.
20. **KI-Lektorat der Untertitel:** Nach der Spracherkennung liest die KI das komplette Transkript wie ein Lektor und gibt es
    Wort für Wort korrigiert zurück (Rechtschreibung, Groß-/Kleinschreibung, Satzzeichen, verhörte Wörter/Namen aus dem
    Zusammenhang: „Schmarm“ → Schmarrn, Kauderwelsch → „Montana Black“). Wirklich Unverständliches bekommt KEINEN Untertitel.
21. **Untertitel-Reihenfolge = Sprechreihenfolge** (nie nach Zeit umsortieren); einzelne Satzzeichen werden nie ein eigener
    Untertitel, sie hängen am Wort davor.
22. **Kurze Clips darf die KI hart kürzen:** Bleibt eine echte Aussage übrig (≥ 2 s, ≥ 4 Wörter), wird alles andere
    (Vorgespräch, „Warte“, Kommentare zum Dreh) konsequent entfernt – ein 25-s-Rohclip kann ein 4-s-Video werden.
23. **Viral = Zoom-Punches + Sounds + Emojis + Hook-Titel** (wie auf der Website versprochen): Whoosh zum Titel und an
    Zoom-Punches, KI setzt 2–6 Akzente an Pointen (Emoji über den Untertiteln + passender Sound: Kaching bei Geld,
    Ding bei Bewertung, Pop sonst), höchstens einer pro 4 s. Sounds selbst erzeugt (worker/make_sfx.py), Emojis aus der
    Apple-Emoji-Schrift des Macs (worker/make_emoji.py, Wunsch des Inhabers; austauschbar).
24. **Große Dateien zuerst verkleinern:** über 1920 px oder über 16 Mbit/s → Arbeitskopie 1920 px / ca. 12 Mbit/s über den
    Grafikchip (VideoToolbox). Kein sichtbarer Verlust im Endformat, alle Schritte danach deutlich schneller.
25. **Rohmaterial bis 5 Minuten = eine Szene:** ganze Geschichte erzählen – Aufhänger → Hauptteil → AUFLÖSUNG (Gewinner,
    Bewertung, Reaktion), nie vor der Auflösung enden („3, 2, 1, los!“). Ziel 30–60 s, max. 90 s; Passagen ohne Sprechen
    (Essen, Warten) fallen als Pause automatisch weg. Erst längeres Material (Podcast, Vlog) → beste Momente als Clips.
26. **Viral-Hook-Titel schreibt die KI** (2–5 Wörter, Kern des Videos, ehrlich) – nie ein abgeschnittener Satzanfang.
    Zerlegte Datums-/Zahlenangaben („11.“ „05.“) werden wieder ein Wort; „&“ bleibt ein eigenes Wort.
27. **Lange Strecken ohne Worte** (Essen, Jubel, Warten, Musik – laut, aber ohne Sprache) werden auf ~2 s gekürzt
    (Anfang 1,2 s + Ende 0,8 s bleiben). Wettessen: 208 s → 58 s, Auflösung inklusive.
28. **Länge wird realistisch geschätzt** (gesprochene Zeit + kurze Luft, wortlose Strecken max. 2 s); über ~95 s strafft die KI
    in einem zweiten Durchgang auf ≤ 75 s – Aufhänger und Auflösung bleiben. Monolog: 4 Min. → 76 s.
29. **Lektorat als Fließtext:** Die KI schreibt den Text wie ein Mensch (Groß-/Kleinschreibung, Satzzeichen, verhörte Wörter),
    die Software ordnet ihn Wort für Wort zu. Stil „Rise“ ist bewusst kleingeschrieben – kein Fehler.
30. **Hörprobe:** Symbole und Zahlen („&“, „11.05.“, Postleitzahlen) werden anders ausgesprochen als geschrieben und zählen
    nicht als „fehlend“; bei sehr kurzen Clips löst ein einzelnes unsicheres Wort keinen Vorsichts-Neuschnitt aus.
31. **Eine Sprache pro Video:** Die Sprache wird einmal aus Anfang, Mitte und Ende bestimmt (knapp → Deutsch) und für JEDE
    Nachprüfung festgehalten. Vorher konnten kurze Schnipsel „englisch“ gehört werden („Thank you.“, „Cheering“, „More“).
32. **Keine Sprech-Stelle ohne Untertitel:** Der Ton im Video wird ausgeglichen (leise Stimmen hoch, laute runter, dann
    −14 LUFS). Die Hörprobe am FERTIGEN Clip trägt alles nach, was dort klar zu hören ist, aber noch keinen Untertitel hat
    (z. B. ein Satz, den die erste Erkennung im Durcheinander überhört hat – Test 08: 5 s Sprechen ohne Untertitel).
    Eine künstlich angehobene Hör-Spur für die erste Erkennung wurde getestet und verworfen (mehr verhörte Wörter).
33. **Unsichtbare Schnitte (Clean):** Sind die Bilder vor und nach dem Schnitt fast gleich (Person hat sich kaum bewegt),
    wird 0,1 s weich überblendet („Smooth Cut“) – der Mini-Sprung beim Rausschneiden von Füllwörtern/Pausen verschwindet.
    Hat sich viel bewegt, gäbe es Geisterbilder (von der KI-Abnahme entdeckt) → sauberer harter Schnitt. Bei großen
    Zeitsprüngen (≥ 1,2 s entfernt) leichter Zoom-Wechsel. Viral bleibt bei harten Zoom-Punches (gewollter Stil).
34. **Stabilisierung wie in CapCut:** Das Kamerawackeln wird gemessen (Hintergrund, Personen ausgeblendet). Ab 3,5 px
    Wackeln wird ausgeglichen (kleiner Zusatz-Zoom ≤ 6 %), gewollte Kameraschwenks bleiben. Stativ-Aufnahmen bleiben unberührt.
35. **Bildausschnitt folgt ruhig:** Bewegt sich die Person, folgt der Ausschnitt erst bei deutlicher Verschiebung (> 7 %) –
    kein Zittern zwischen den Schnitten.
36. **Emojis (Viral):** echte Apple-Emojis, 140 px, scharf (nur verkleinert), UNTER den Untertiteln, gleiten abwechselnd von
    links/rechts/unten/oben sanft herein und wieder hinaus.
37. **Titel oben (Viral):** weiße Karte mit runden Ecken und weichem Schatten, klare Schrift in normaler Schreibweise,
    zwei ausgewogene Zeilen, Emoji am Ende als echtes Apple-Emoji; gleitet sanft herein, bleibt 3 s.

38. **Clean hat auch einen Titel oben:** dezente dunkle Glas-Karte, weiße Schrift, ohne Emoji, sachlich-elegant
    („Kaiserschmarrn in Mannheim“) – klar schwächer als Viral, aber nicht langweilig.
39. **KI-Abnahme vor jeder Auslieferung:** Die KI schaut sich den FERTIGEN Clip mit Ton an wie ein Chef-Cutter (Note,
    Probleme mit Sekunde). Falsch verstandene Untertitel korrigiert sie selbst („Shilling David“ → „Shirin David“, fehlendes
    „Matcha“), dann wird neu eingebrannt. Alle übrigen Befunde landen im Protokoll → daraus entstehen neue Regeln.
40. **Gemurmel zählt wie Stille:** Strecken, in denen nur unverständlich genuschelt/gekaut wird (keine Untertitel), werden
    wie wortlose Strecken auf ~2 s gerafft (Test 01: 12 s stummes Kauen).

41. **Kamerafahrt statt Standbild (aus Tutorials):** Jeder Abschnitt fährt ganz langsam nach vorne (Clean ≈ 3,5 % in 5 s,
    max. 5 %), zentriert auf das Gesicht – das Bild lebt, Sprünge fallen weniger auf. Über weiche Übergänge läuft die
    Fahrt weiter (kein Geisterbild), ein harter Schnitt setzt sie zurück.
42. **Zooms betonen wichtige Sätze (Viral):** Harte Zoom-Punches (+12 %) liegen genau auf den stärksten Momenten, die die
    KI auch für Emoji/Sound auswählt, IMMER einer in den ersten 5 Sekunden (Hook). Die Zooms an den Schnitten sind dafür
    dezenter (1,0 / 1,1 / 1,04 / 1,14).
43. **Spannung vor der Auflösung (Viral):** leiser Riser, der genau in den letzten großen Moment (Bewertung/Preis/Gewinner)
    läuft.
44. **Ton wie vom Profi:** Hintergrundrauschen leicht reduzieren (Straße, Wind), Stimmen angleichen, −14 LUFS, weicher
    Ein- und Ausklang (kein Knacken, kein abgehackter letzter Ton).

45. **Zoom-Rhythmus (Viral/Premium, aus 29 TikToks):** spätestens alle ~3 s ein Zoom-Wechsel (100 % ↔ 112 %) an
    Satzanfängen oder betonten Wörtern, zusätzlich zu den Punches auf den Pointen.
46. **Premium = Motion Graphics** (Webseite: „3D-Titel, Motion Graphics, Zähler“):
    - 3D-Titel oben (0–3 s): Großbuchstaben, zwei ausgewogene Zeilen, Tiefe in dunkler Akzentfarbe, poppt auf;
    - an den stärksten Momenten großes Schlüsselwort mit 3D-Tiefe (1,1 s), darüber klein die Wörter davor
      („grad vom“ / BAGELETTE) = Kinetic Typography;
    - Zahlen zählen hoch (0,75 s, Jahreszahlen ab −15), bleiben kurz stehen;
    - Grafiken weichen Gesichtern aus (unter das Kinn, sonst über den Kopf);
    - Whoosh zu jeder Grafik, Riser vor der Auflösung, Kino-Look (Kontrast, weiche Vignette, feine Körnung);
    - keine Emojis (edler als Viral).

47. **KI-Abnahme ergänzt auch:** Was im fertigen Clip klar gesprochen wird, aber keinen Untertitel hat, trägt sie selbst
    nach („Da gebe ich sogar eine 9“, „Matcha“) – nur in echten Lücken. Nach Korrekturen prüft sie ein zweites Mal
    (Endnote). Test 29.09.: 01 von 7 auf 9, 08 und 05 alle Befunde behoben (Namen, Hausnummer, fehlende Wörter).
48. **Weggeraffte Pausen bleiben weg:** Ein Mini-Stück (< 0,3 s) hinter einer großen Lücke wird nie mit dem Abschnitt
    davor verbunden (das hatte die 10-s-Kau-Pause in 01 wieder eingefügt) – es wird stattdessen nach vorne verlängert.
49. **Untertitel verschwinden spätestens 1,2 s nach dem Wort**, auch wenn das nächste Wort erst viel später kommt.
50. **Premium-Schlüsselwörter werden nie abgeschnitten:** lange Begriffe → zwei ausgewogene Zeilen.
51. **Bild subpixel-genau (30.09.):** Ausschnitt, Zoomfahrt, Punches und Stabilisierung werden je Frame in einem Schritt
    berechnet (OpenCV, bikubisch) – früher auf ganze Pixel gerundet → beim Sony-Querformat 3,6-px-Stufen = Mikro-Ruckeln.
52. **Stabilisierung über Schnitte hinweg:** Kamerabahn (x, y, Drehung) des Hintergrunds für den fertigen Clip am Stück,
    stark geglättet (Clean σ 1,4 s ≈ Stativ, Viral/Premium 1,0 s). Der Sprung des Hintergrunds an Jump-Cuts
    (Kamera in der weggeschnittenen Zeit verrutscht, gemessen 6–11 px in Test 01) wird ausgeglichen. Personen werden
    ausgeblendet; ohne erkanntes Gesicht die Bildmitte (Test 07: gemustertes Hemd wurde sonst „stabilisiert“).
53. **Ausschnitt folgt nur, wenn nötig:** Bewegt sich die Person, bleibt der Ausschnitt stehen, solange das Gesicht
    bequem im Bild ist (±20 % Breite, ±10 % Höhe); geplant wird in stabilisierten Koordinaten (sonst doppelte Korrektur).
54. **Untertitel wackeln nie:** Jedes Wort hat eine feste Position (Breite mit der echten Schrift gemessen); das
    gesprochene Wort wird farbig und wächst nur um seine eigene Mitte. Zeilen nach echter Breite (max. 840 px, frei für
    die TikTok-Knöpfe), immer einzeilig, neue Zeile nach Satzzeichen und Sprechpausen (> 0,45 s).
55. **Untertitel-Größe je Video:** Stilgröße, außer lange Wörter sprengen oft die Zeile (mehr als jede 7. Zeile wird
    nur aus Platzmangel umgebrochen) → schrittweise bis 84 % kleiner. Umbrüche an Pausen/Satzzeichen zählen nicht.
    Ein einzelnes Riesenwort wird nur in seiner eigenen Zeile verkleinert.
56. **Stabilisierung im „Schlauch“:** Die ruhige Bahn bleibt immer so nah an der echten, wie der Rand es erlaubt
    (Test 01: vorher stieß die Korrektur in 44 % der Bilder an den Bildrand → Ruckler; jetzt 0,6 %). Der Ausschnitt
    wird nie ganz an den Bildrand gelegt, damit dort Platz zum Ausgleichen bleibt.

57. **Quiz-Punktestand (aus den Malo's-Videos des Nutzers):** Erkennt die KI ein Straßen-Quiz, steht ab der ersten Frage
    groß „0/N“ in der Bildmitte (über den Untertiteln); bei jeder richtigen Antwort springt er grün auf (Pop + Ding),
    danach wieder weiß. Alle Stile.
58. **Lichtleck-Übergang (Viral/Premium):** An großen Zeitsprüngen (≥ 3 s weggeschnitten, z. B. neue Frage) zieht ein warmer
    Schimmer (orange → rosa) 0,55 s durchs Bild – höchstens alle 6 s, max. 4 je Clip.
59. **Tempo wie der Nutzer (Vergleich Rohmaterial C4729 ↔ „malos umfrage 1“: 117 s → 51 s):** kurze Begrüßung, dann sofort
    der Aufhänger; Rate-/Verhandlungsschleifen auf die letzten 1–2 Tipps + Auflösung; Füllsätze („Okay.“, „Ja.“, „Mach auf.“)
    zwischen Abschnitten raus; Ende nach dem Gewinn + höchstens einem Satz, kein Verabschiedungs-Pingpong.
    Lücke an Schnitten beim Nutzer: Median 0,09 s, oberes Viertel 0,2–0,4 s.

60. **Hintergrundmusik (Test, `--music auto`):** 10 lizenzfreie Pixabay-Stücke OHNE Content ID (schnitt-maschine/musik,
    LIZENZ.txt). Die KI wählt nach Inhalt/Stimmung; Musik auf −24 LUFS, duckt per Sidechain unter die Stimme, 0,4 s rein,
    1,2 s raus, Limiter am Ende. Standard in der App bleibt vorerst „keine Musik“.
61. **Kunden-Logo (Test, `--logo`):** oben links (190 px, 48/120 px vom Rand), blendet weich ein und bleibt; Titel-Karte und
    Premium-3D-Titel rutschen darunter. In der App fehlt noch der Upload.

62. **Stimme hervorheben (Nutzerwunsch 30.09.):** vor dem Angleichen Hochpass 80 Hz (Brummen/Wind), −1,5 dB bei 220 Hz
    (dumpf), +2 dB bei 3 kHz (Verständlichkeit). A/B-Test: Straßen-Umfrage 08 → Whisper erkennt 59 statt 22 Wörter (Ø-Sicherheit
    0,50 → 0,66), KI-Blindhörtest „klarer“ bzw. gleich. Stärkere EQ/Kompression (+3,5 dB, Kompressor 3:1, De-Esser) klang
    blechern und verschlechterte die Erkennung → verworfen. Musik duckt zusätzlich unter die Stimme (Regel 60).

63. **Schnitt-Reparatur (01.10.):** (a) Clip beginnt/endet immer an Satzgrenzen (Ende bis +8 s verlängern, Anfang bis −5 s);
    (b) Hörprobe: Sprache vor dem ersten / nach dem letzten Untertitel (Wortfetzen „Kennt ihr sch-“, „Mhh“) wird
    weggeschnitten; (c) meldet die KI-Abnahme Schnittfehler (schnitt/anfang/ende), korrigiert die KI die Satzauswahl mit
    genau diesem Hinweis und es wird EINMAL neu geschnitten + neu geprüft. Malo-Quiz: Note 5 → 7.
64. **Zerhackte Zahlen:** Untertitel-Korrektur „9, 80.“ → „89“ wird übernommen (Summe/gleiche Ziffern), obwohl die Texte
    unähnlich aussehen.
65. **Länge wie der Nutzer (20 Vorher-Nachher-Paare aus „exklusiv marketing“, 01.10.):** Straßen-Umfrage mit einer Person
    = 15–25 s (25–35 % des Rohmaterials); Ablauf Herkunft/Hook → Probieren → Bewertung → Preis raten + Auflösung; Ende hart
    auf Auflösung/„kommt vorbei“ (kein Tschüss/Ciao); Regel-Erklärungen („1 ist schlecht, 10 ist gut“), Rückfragen und
    Mikro-Tests raus; bei Mehrfach-Takes nur der letzte. Lücke an Schnitten beim Nutzer: Median 0,08 s (384 Schnitte).
66. **Prüfstand (app/worker/pruefstand.py):** 10 Umfrage-Rohclips → Pipeline → Vergleich mit dem Schnitt des Nutzers
    (Länge, Precision = was wir drin haben, hätte er auch; Recall = was er drin hat, haben wir auch). Basis 01.10.:
    Länge 1,47×, Precision 0,76, Recall 0,87 (F1 0,81). Jede Änderung an Story/Schnitt hier gegenmessen.

67. **Tempo wie der Nutzer:** Er beschleunigt Umfragen im Mittel ×1,27 (fertig ≈ 6,3 Silben/s, roh ≈ 5,5). Pipeline misst die
    Sprechgeschwindigkeit der behaltenen Wörter: Ziel Viral/Premium 6,6 Silben/s (max. ×1,3), Clean 6,2 (max. ×1,2), nie
    langsamer; erst ab 4 s zusammenhängender Sprache; Ton per atempo (Tonhöhe bleibt), Bild läuft im gleichen Tempo.
68. **Leise Pausen auf lauten Straßen:** Eine Lücke zwischen zwei Wörtern (≥ 0,3 s), deren Pegel ≥ 6 dB unter dem der
    Stimmen liegt, zählt als Pause und wird geschnitten (vorher nur echte Stille). Wortlose Strecken (Kauen) zeigen nur
    noch 0,8 s + 0,5 s statt 1,2 s + 0,8 s.
69. **Klarheit vor Vollständigkeit, aber Kern bleibt:** Preis raten + Auflösung bleiben, wenn in ≤ ~3 Wortwechseln/8 s
    erledigt („5 Euro? – Nein, 3,99“); ufert es aus (Missverständnis, Rückfragen, andere Produkte), ganz raus (Nutzer
    Umfrage 16: 93 s → 15 s). Ortsangabe/„kommt vorbei“ am Ende bleibt.
70. **Satzauswahl stabil:** Die KI entscheidet dreimal parallel, ein Satz bleibt bei Mehrheit; sie nennt Format und
    Ziel-Länge – liegt der Schnitt deutlich darüber (> 1,3× bzw. +6 s), wird gezielt auf das Ziel gekürzt.
71. **Namen & Marken (Studio-Feld, im Konto gespeichert):** gehen als Vorgabe in die Spracherkennung (initial_prompt),
    ins Lektorat und in die KI-Abnahme – gegen „ein“ statt „Nutella“, „Ginti & Mo“ statt „Ginti & More“.

72. **Zwischenschnitte genau auf das Wort (Ordner, 03.10.):** B-Roll-Material sind auch Stellen OHNE Sprache in
    Sprech-Clips (≥ 1,5 s, z. B. Corndog in die Kamera halten); die KI beschreibt je Stelle ein Bild. Wird etwas genannt,
    das es als Material gibt, erscheint es genau bei diesem Wort („Patty“ → Patty auf dem Grill), 1–2,5 s, etwa alle
    4–7 s, nie zwei hintereinander, nicht in den ersten 1,5 s. Produktbilder ebenso auf das Wort.
73. **Musik-Schnitt (nur B-Roll, niemand spricht – Ordner oder Einzelvideo):** musikschnitt.py. Stellen à ~2,5 s, KI
    ordnet: Aufhänger (stärkstes Bild) → Zubereitung in echter Reihenfolge → fertiges Produkt/Laden länger. Beat aus der
    Musik (Spektral-Flux + Autokorrelation), Schnitt exakt auf den Beat, ~0,85 s je Bild (Aufhänger ~1,3 s, Held/Ende ~2 s),
    max. ~22 s. Sanfter Push-in/Pull-out im Wechsel, zu kurze Stellen leichte Zeitlupe (≥ 0,6×). Titel-Karte + Logo wie
    sonst, keine Untertitel, Originalton (Brutzeln) leise unter der Musik, −14 LUFS.
74. **Doppelt-Check nach der Satzauswahl:** dieselbe Frage zweimal (auch anders formuliert), korrigierte/wiederholte
    Antworten („8. – 9.“) → nur die letzte Fassung. Zwei KI-Durchläufe, raus nur bei Übereinstimmung, max. 30 %, nie der
    letzte Satz. Prüfstand 03.10.: Länge 1,14 → 1,10 × Nutzer, F1 0,803 → 0,806 (Umfrage 16: 21,6 → 14,1 s, Nutzer 14,8).
75. **Geflüster vom Team (Ordner):** Whisper verschluckt geflüsterte Regieanweisungen („sag jetzt: …“) und dehnt dafür
    ein Wort über Sekunden. Wörter > 1 s werden auf ihren lautesten Teil gekürzt, wenn der Rest deutlich leiser ist;
    Stücke werden an Pausen > 0,9 s geteilt → das Geflüster fällt heraus (Malo's Junior Box: Abnahme 4 → 8/10).
76. **Letztes Wort nie abschneiden:** Ränder der Stücke reichen bis kurz vor das Nachbarwort (Videoende +1,3 s); im
    Schnitt bekommt das letzte Wort 0,45 s Ausklang, auch wenn die Stille-Erkennung früher „Stille“ meldet.

77. **Ordner mit mehreren Themen:** je Thema ein eigenes Video, höchstens so viele wie „Anzahl Clips“ (montage.py
    --clips), kein Satz in zwei Videos; ist alles ein Thema, nur ein Video. Fehlende Videos werden anteilig erstattet.
78. **Gestrichene Wörter als EIN Stück schneiden:** aufeinanderfolgende gestrichene Wörter werden zusammengefasst –
    einzeln war jedes kürzer als die Mindest-Schnittlänge und blieb drin (Fehlstarts kamen so durch).
79. **Neustart mit Signalwort:** „ihr wolltet – warte – ihr wolltet Strawberry“ → erster Anlauf samt „warte/sorry/
    Moment/stopp/nochmal/nee“ raus, auch wenn nur 2 Wörter gleich sind; schlägt den Schutz „letzte Fassung“.

### Rückmeldung des Nutzers vom 05.10.2026 zu den Testvideos
80. **Nicht zu schnell:** Ordner-Videos und Erklär-/Zubereitungs-Videos höchstens leicht beschleunigen (Malo's Junior Box
    war mit ×1,3 „zu schnell“).
81. **Zubereitung in echter Reihenfolge, vollständig:** roh → braten/smashen → belegen → verpacken → fertig. Clips, in
    denen jemand beim Zubereiten redet, gehören dazu. Ein Zwischenschnitt darf keinen späteren Schritt vorwegnehmen
    (nicht das gebratene Patty zeigen, bevor es gesmasht wird).
82. **Ein Video = ein Thema:** Clips zuerst Themen/Videos zuordnen (Juniorbox ≠ „bester Burger in Mannheim“), nur Clips
    aus EINEM Thema verwenden. Nach dem Schlusssatz/Aufruf (z. B. „… und dazu eine Capri-Sonne“) ist Schluss.
83. **Gesicht nie verdecken:** Sticker, Logos, Kommentar-Einblendungen nie über das Gesicht – lieber kleiner und daneben.
84. **Zooms auf Produkte:** wenn ein Produkt genannt/gezeigt wird („Classic“, „Topping nach Wahl“) – Punch-in.
85. **Musik-Schnitt nur saubere Bilder:** keine Stelle, in der jemand wegschaut, ins Bild greift/den Teller wegzieht,
    eine fremde Person auftaucht oder jemand lacht/redet, ohne dass es zum Video gehört. Ganze Stelle prüfen, nicht
    nur ein Bild.

86. **Gesicht immer frei (Kollege des Nutzers, 05.10.):** Über das Gesicht kommt nie etwas – Untertitel, Emojis,
    Titel-Karte, Logo, Sticker, Motion Graphics. Gemeinsamer Gesichts-Bereich (face_box: 10.–90. Perzentil über den
    ganzen Clip + Rand), alle Einblendungen weichen aus (Logo nach rechts, Titel über/unter das Gesicht, Emojis über
    die Untertitel oder neben den Kopf, Sticker in den größten freien Bereich).
87. **Sinn-Check am Schluss:** Das fertige Video wird abgehört (Whisper) und gegen das Rohmaterial geprüft: Sinn,
    Reihenfolge, fehlende Antworten/Schritte, Sprünge, Versprecher, Füllwörter, Wortfetzen, Schluss. Bei Problemen
    einmal automatisch nachschneiden und nochmal prüfen.
88. **Deko-Grafiken nie als Sticker:** Pfeile, Icons, Rahmen aus dem Ordner werden nicht eingeblendet (Pfeil an der Hand
    „passt nicht“). **Leise Antworten hörbar:** Lautstärke-Angleich bis ×12 („2 Euro“ ging unter).
89. **Musik-Schnitt mit Fotos (Privat/Urlaub):** Fotos als Standbild mit sanftem Zoom, Querformat mit unscharfem
    Hintergrund; Erzählung wie eine Reise (Ankommen → Erlebnisse → Highlight → Abschluss).

## Aus den Malo's-Videos des Nutzers (30.09.2026, material/vorher-nachher, Analyse in analyse/)
| Muster | Stand |
|---|---|
| Umfragen: Stativ, weite Einstellung, fast keine Zooms | Clean so umgesetzt (Stabilisierung, Zoom nur bei großen Sprüngen) |
| Untertitel klein (≈ 5 % Bildhöhe), 1–3 Wörter, weiß, Schlüsselwörter gelb/grün | Größe je Video; Farbakzente teilweise |
| Logo oben links + „Quiz“ | offen – bräuchte Kunden-Logo-Upload in der App |
| Punktestand x/5, grün bei Treffer, grüner Haken | Regel 57 (Haken offen) |
| Lichtleck / weißer Blitz an Szenenwechseln | Regel 58 (Blitz offen) |
| Food-B-Roll: Schnitt alle 0,7–1 s auf den Beat, Gimbal-Fahrten, keine Untertitel | umgesetzt (Regel 73, Musik-Schnitt) |
| Freigestellte Produktbilder/Sticker, Pfeile, Preis-Schild | offen |
| Musik in jedem Video (Hip-Hop/Lo-Fi-Beat) | offen – Lizenzfrage |

## Aus 29 TikTok-Vorbildern (29.09.2026, material/tiktok-vorbilder, Auswertung in analyse/)
| Muster (fast überall) | Stand |
|---|---|
| Untertitel 1–3 Wörter, fette serifenlose Schrift, weiß, weicher Schatten statt dicker Kontur, Y ≈ 60–78 %, Schlüsselwörter farbig | vorhanden (Stile); weicher Schatten: Stil „rise“/„serif“ |
| Punch-Zooms 110–115 % alle 2–4 s, langsame Push-Ins 5–10 % | umgesetzt (Regeln 41, 42, 45) |
| Hook-Text oben in den ersten 2–3 s, Schlüsselwort farbig | umgesetzt (Titel-Karte / 3D-Titel) |
| Whoosh/Pop/Klick zu jeder Grafik, Riser vor Höhepunkt | umgesetzt |
| Große Schlüsselwörter, hochzählende Zahlen | Premium (Regel 46) |
| Leise Hintergrundmusik (−16 bis −22 dB), Beat-Drop an der Pointe | offen – braucht lizenzfreie Musik |
| B-Roll/Schnittbilder über dem Sprechen (z. B. Essen, Laden) | umgesetzt für Ordner (Regel 72) |
| Vollbild-Typo-Karten, Listen-Grafiken, Split-Screen, Handy-Mockups | später (Premium-Ausbau) |
| Schwarz-Weiß/Letterbox-Looks (Motivations-Edits) | passt nicht zu Kundenvideos (Restaurants/Umfragen) |

## Aus den Cutting-Tutorials (29.09.2026, 6 Videos in material/tutorials, Transkripte in transkripte/)
| Tutorial-Regel | Stand |
|---|---|
| Hook in den ersten 5 s, Stille raus, direkt zum Höhepunkt, keine Anlauf-/Antiklimax-Teile | umgesetzt (Regeln 16, 25–28) |
| Jump Cuts bei Talking Heads, Schnitt direkt vor Stille/vor dem Sprechen | umgesetzt (Regeln 7–13) |
| Zooms für wichtige Aussagen, einer in den ersten 5 s | umgesetzt (Regel 42) |
| Untertitel Wort für Wort, 1–3 Wörter, Schlüsselwort farbig | umgesetzt (Stile) |
| Sounddesign: Whoosh für Bewegung, Kaching für Geld, Hit/Riser für Spannung, leise (−10 dB) | umgesetzt (Regeln 23, 43) |
| Rauschen reduzieren, klare Stimme, Balance | umgesetzt (Regeln 32, 44) |
| Überblendung (Dissolve) = Zeitsprung; Fade am Ende | umgesetzt (Regeln 33, 44) |
| Einheitlicher Look: wenige Schriften/Farben, eigener Wiedererkennungswert | umgesetzt (1 Schrift je Stil) |
| Motiv folgt der Person („Tracking“), Motiv mittig | teilweise (Regel 35) – fließendes Mitführen im Abschnitt: offen |
| Animierter Text statt Standard-Untertitel im Hook, Schlüsselwörter groß mit Glow, Icons, B-Roll, Zähler | **Premium** (offen) |
| Musik unterlegen, an Pointen kurz stoppen | offen – braucht lizenzfreie Musik |
| Stärkste Aussage als Teaser vorne („Cold Open“) | Idee für später |

## Stile
| | Clean | Viral |
|---|---|---|
| Schnitte | 0,1 s weich überblendet (nur bei fast gleichem Bild); Zoom-Wechsel (1,07) bei großen Sprüngen; langsame Kamerafahrt | harte Schnitt-Zooms 1,0 / 1,1 / 1,04 / 1,14 + Punches (+12 %) auf Pointen |
| Titel | – | Titel-Karte (runde Ecken) die ersten 3 s |
| Extras | – | Sounds (Whoosh/Pop/Ding/Kaching), Apple-Emojis unter der Schrift |
| Premium | – | wie Viral + 3D-Titel, große Schlüsselwörter, Zähler, Kino-Look, keine Emojis (Regel 46) |

## Rückmeldungen & daraus entstandene Regeln
| Datum | Video | Rückmeldung | Regel / Änderung |
|---|---|---|---|
| 27.09.2026 | Nunu-Selfie (52 s, dunkel) | Rohmaterial kam ungeschnitten raus, keine Untertitel | provisorische Pipeline ersetzt; Qualitäts-Check eingebaut |
| 27.09.2026 | dto. | Untertitel zu klein, lagen auf dem Kinn | Größen wie Website-Vorschau, Position weicht Gesicht aus |
| 27.09.2026 | dto. | Video zu dunkel | automatische Aufhellung nach gemessener Helligkeit |
| 28.09.2026 | dto. | Test: Wortenden abgeschnitten („Novosmir“, „dabei“ fehlte, „Durchziele“) | Regeln 7, 8, 11 |
| 28.09.2026 | Kaiserschmarrn-Straßeninterview (Kollege) | Start mit Vorbereitung/„nochmal von vorne“, Ende mit „okay super, passt?“ zum Kameramann, Übergänge abgehackt, Untertitel sollen genau passen und nicht springen | Regeln 12, 13, 14 – Ergebnis: Szene startet bei „Hallo, wollt ihr …“, endet bei „Tschüss“, 4 weiche Schnitte |
| 28.09.2026 | Kaiserschmarrn (2. Runde) | Neu-Schnitt hatte immer noch „Nehmt ihr dann den?“ am Anfang und „passt?“ am Ende; Kunden laden komplette Rohdateien mit Versprechern/Wiederholungen hoch → „wie ein persönlicher Cutter“ denken | Fehler behoben (KI-Story scheiterte immer still) · Regeln 16–19 · Ergebnis: startet bei „Wollt ihr einen Kaiserschmarrn probieren?“, endet bei „Tschüss“ |
| 28.09.2026 | Nunu-Selfie mit künstlichem Versprecher | „Wir sind 21 Jahre alt aus Stuttgart“ zweimal gesagt, Spracherkennung hörte es nur einmal | Regel 18 + Schutz der letzten Fassung (vorher flogen beide Anläufe raus) · Ergebnis: Satz genau einmal, vollständig |
| 28.09.2026 | Testlauf Runde 1 (8 Videos aus Exklusiv Marketing: Umfragen Sony/DJI, Monologe) | Straßenlärm → verhörte Wörter/Namen, Wörter vertauscht, einzelne Satzzeichen als Untertitel, alles kleingeschrieben; Kurzclip mit Kommentaren zum Dreh nicht gekürzt; Viral ohne Sounds/Emojis | Regeln 20–23 |
| 28.09.2026 | Testlauf Runde 1 (Fortsetzung) | Wettessen endete bei „los!“ (Auflösung fehlte), Hook = abgeschnittener Satzanfang, „PIZZA& PAINT“, Datum zerlegt, Kleinschreibung blieb, 2,5-GB-Datei über Upload-Grenze | Regeln 24–26, Upload-Grenze 4 GB |
| 29.09.2026 | Testlauf Runde 1 (Abschluss) | Wettessen 150 s mit minutenlangem Essen, Monolog 3½ Min., Kleinschreibung trotz Lektorat, Hörprobe stolperte über „&“/Datum | Regeln 27–30 · Ergebnis: 58 s / 76 s, alle 8 Videos bestehen |
| 29.09.2026 | Runde 1 – Rückmeldung Inhaber | Schnitte wackeln/springen sichtbar, Kamera wackelt, bei leisen Sprechern fehlen Untertitel, teils andere Sprache, Emojis unscharf/zu groß/über der Schrift, Titel-Kasten oben „viereckig“ und langweilig | Regeln 31–37 |
| 29.09.2026 | Clean-Wunsch + KI-Abnahme Runde 2 | Clean soll auch eine ordentliche Caption oben haben; Abnahme fand Geisterbilder bei Überblendungen, falsche Namen, 12 s stummes Kauen | Regeln 33 (angepasst), 38–40 |
| 28.09.2026 | dto. | Test: „Ähm“ nach „Stand jetzt“ blieb hörbar, gedehntes „aaaber“/„uuund“ | Regeln 9, 10 – Ergebnis: 51,4 → 44,8 s, keine Füllwörter, alle Wörter da |

90. **Urlaub/Privat braucht Ruhe.** Im Musik-Schnitt stehen Bilder bei Privat-Videos ~1,2 s (Held/Ende ~2,4 s), Ziel 20–30 s; Videos tragen das Video, Fotos nur dazwischen. Premium-Stil: Kino-Look (weiche Vignette, Ein-/Ausblenden) und Serifen-Titel ohne Kasten.
91. **Der Untertitel zeigt, was gesagt wurde.** Das KI-Lektorat darf Rechtschreibung, Großschreibung und Satzzeichen ändern; ein anderes Wort nur, wenn es fast gleich klingt (verhört) oder ein Kunden-Name ist. Nie umformulieren. (Nutzer 05.10.: „im Untertitel steht etwas anderes, als er gesagt hat“.)
92. **Nichts wackelt.** (Nutzer 05.10.: „da darf nichts ruckeln, nichts wackeln“.) Sprech-Videos: Glättung 2,6 s, bis 20 % Ausgleichs-Zoom, jede Einstellung für sich beruhigt (harte Schnitte im Material werden erkannt – die Glättung läuft nicht über Übergänge), ohne Gesicht wird das ganze Bild verfolgt. Musik-Schnitt: jede Einstellung einzeln stabilisiert (vidstab, auf Arbeitsgröße 1080 breit/30 fps).
93. **Stabil, aber nicht reingezoomt.** (Nutzer 05.10.) Kein Zusatz-Zoom für die Stabilisierung. Der Ausschnitt gleicht bis ± ~7 % aus und darf dabei über den Bildrand – der fehlende Streifen wird gespiegelt (Sprech-Videos) bzw. aus den Nachbarbildern gefüllt (Musik-Schnitt). Der langsame Stil-Zoom im Musik-Schnitt ist auf 4 % reduziert.
94. **Der ruhigste Teil gewinnt.** Im Musik-Schnitt wird aus jeder Stelle der Abschnitt mit dem wenigsten Ruckeln genommen (Bewegungsmessung), nicht einfach die Mitte. Ausgleich bis 120 px mit gespiegeltem Rand, weiterhin ohne Zoom.
95. **Der Nachschnitt wirft nichts Wesentliches weg.** Nach dem Sinn-Check wird ein Nachschnitt nur übernommen, wenn das Video dadurch höchstens 15 % kürzer wird – sonst bleibt die bereits geprüfte Fassung.
96. **Kein Schwenk ohne Inhalt.** (Nutzer 05.10., Malo's Sek. 48–54.) Schwenkt die Kamera ≥ 1,5 s nur von A nach B, wird die Strecke herausgeschnitten – samt dem, was dabei nebenbei gesagt wird. Der Schnitt setzt wieder ein, wo der nächste Satz beginnt („wir machen jetzt zwei Gurken“).
97. **Jede Einstellung beginnt mit der Handlung.** (Nutzer 06.10.: „nicht den Schwung der Kamera mitnehmen – das ist nicht professionell“.) Auch kurze Schwenks (≥ 0,8 s) werden herausgeschnitten, wenn dabei niemand spricht; lange (≥ 1,5 s) wie Regel 96 samt dem Nebenbei-Gesagten.
98. **Ganz oder gar nicht.** Angefangene Sätze, die nicht zu Ende gesprochen werden, fliegen ganz raus. Ein Satz bleibt nur, wenn Bild und Wort zusammenpassen („wenn das Brot fertig ist“ über dem Patty → Satz weg). Keine Einstellung, die nur kurz aufblitzt. Diese Regel streicht nie einen ganzen Zubereitungsschritt: passt das Bild nicht, kommt passendes B-Roll darüber oder nur der eine Satz fällt weg (Test 06.10.: erste Fassung kürzte Malo's auf 39 s ohne Käse, Soße, Belegen – korrigiert).
99. **Die Abnahme ändert keine Zahlen.** (Testlauf 06.10., Pizza & Paint: Runde 1 formatierte „1105.“ richtig zu „11.05.“, Runde 2 machte daraus „12.05.“ – falsches Veranstaltungsdatum, Sinn-Kontrolle meldete trotzdem „ok“.) Untertitel-Korrekturen mit Ziffern werden nur übernommen, wenn dieselben Ziffern bleiben (Umformatieren). Abgelehntes steht in STATS `zahl_korrektur_abgelehnt`. **Offener Zielkonflikt:** Damit bleiben auch echte Verhörer bei Zahlen stehen (alte Tests: „730“ statt „37“, dreimal richtig korrigiert). Bessere Lösung, noch nicht gebaut: Zahl-Korrektur nur, wenn die Hörprobe des fertigen Tons sie bestätigt, und nie eine Stelle zweimal ändern.
100. **Emojis verdecken auch keinen Untertitel.** (Testlauf 06.10.: 🎁 lag auf „Muttertag“.) Die alte Kappung auf y = 1660 entfällt; Mindestabstand zur Untertitelzeile, sonst Ausweichposition, sonst kein Emoji.
101. **Die Abnahme schreibt nichts in den Untertitel, was nicht gesagt wurde.** (Testlauf 06.10.: „lecker.“ → „Richtig lecker.“ – gesprochen wird „Mhh, lecker“, mit lokaler Hörprobe belegt.) Es gibt DREI Wege, auf denen KI-Text in Untertitel kommt: Lektorat (Regel 91), Abnahme-Korrekturen, Abnahme-Ergänzungen. Die letzten beiden waren kaum geprüft. Jetzt: Korrekturen nur als SCHREIBWEISE desselben Worts (gleiche Wortzahl, je Wort ähnlich, keine Artikel/Kurzwörter ändern, Zahlen nur umformatieren – Regel 99); Ergänzungen sind abgeschaltet (`CAPTION_ADDS = False`) und werden nur protokolliert. Getestet am eingebauten Code mit 13 Fällen + 1 Ergänzung, noch nicht an einem vollständigen Video. **Preis:** echte Verhörer mit anderem Wortlaut („Grat“ statt „Kruste“, „730“ statt „37“) bleiben stehen. **Richtige Lösung, noch nicht gebaut:** jede Korrektur/Ergänzung nur übernehmen, wenn die lokale Hörprobe (`hear`, kostet kein KI-Kontingent) sie an der Stelle bestätigt. Merksatz: Noten der KI-Abnahme und „Sinn-Check ok“ sind kein Beleg – beide Fehler wurden mit Note 8–9 durchgewinkt.
102. **Emojis sind vollständig.** (Nutzer 06.10.: „bei den meisten Videos oben abgeschnitten“.) Ursache war nicht die Position: alle 32 Dateien in `schnitt-maschine/emoji` waren selbst oben gekappt (Apple-Emoji sitzt ~18 px über der Schriftmitte, Blatt war zu klein); dasselbe im Titel-Emoji. Jetzt groß gerendert, auf die sichtbare Grafik zugeschnitten, mittig mit 8 px Rand. Alte Dateien: `emoji/_alt_beschnitten`.
103. **Umfrage endet mit kurzem Abschied; Ordner = Puzzleteile eines Videos.** (Nutzer 06.10.) (a) Bei Gesprächen/Umfragen bleibt EIN kurzer Abschied („Dankeschön!“ – „Tschüss!“) als Schlusspunkt – kehrt die frühere Regel „hart auf der Auflösung enden“ um; Monolog/Promo unverändert. (b) Ordner-Schnitt nach der Arbeitsweise des Inhabers: alle Clips ansehen → in Drehreihenfolge in die Timeline → erst dann streichen, nur mit Grund. Messbar im Montage-Protokoll: „Abdeckung C4: x von y Sätzen“ je Sprech-Clip, mit Warnung unter 50 % (Hinweis zum Nachsehen, kein Urteil – ein Clip voller Fehlversuche darf wenig beitragen; stumme Arbeitsschritte misst es nicht).
104. **Was korrigiert wurde, bleibt korrigiert.** (Testlauf 06.10., Cheesecake: Protokoll „Vollmilzschokolade → Vollmilchschokolade korrigiert“, im Video stand weiter „Vollmilzschokolade“.) Der Nachschnitt nach dem Sinn-Check (seit 05.10.) brannte die frischen Untertitel des neuen Schnitts ein und verwarf damit alle Korrekturen der Abnahme. Jetzt werden sie nach dem Nachschnitt erneut eingesetzt (`reapply_fixes`, Stelle wird im neuen Text gesucht, Regeln 99/101 gelten wieder). Funktion an 6 Fällen geprüft; im Ablauf erst belegt, wenn ein Lauf Korrektur UND Nachschnitt hat. Folge für die Deutung älterer Videos: stehengebliebene Verhörer in den Malo's-Fassungen vom 05.10. („Grat“, „der Brot“) können von diesem Fehler stammen, nicht von Regel 91.
105. **Ordner mit mehreren Themen: nachvollziehbar.** (Testlauf 06.10.: Cheesecake-Ordner ergab 1 Video ohne Produktbilder; am 03.10. waren es 2 Videos mit 2 + 3 Bildern, Satzauswahl des ersten fast gleich.) Ursache nicht mehr feststellbar – es gab kein Protokoll. Jetzt meldet die Montage, wie viele Geschichten die KI liefert (mit Sätzen/Bildern/B-Rolls) und was die Filter verwerfen. Die Formulierung vom Mittag („keine Einstellung unter ~1,5 s“) kollidierte mit Zwischenschnitten und Produktbildern und ist auf Sprech-Abschnitte eingegrenzt.
106. **Pannen gehören nie ins Video.** (Inhaber 06.10.: „‚das Fleisch ist verbrannt‘ darf man nicht hören – egal ob Clean, Pro oder Premium.“) Alles, was der Sprecher über ein Missgeschick oder seine Unsicherheit sagt („vielleicht verbrannt“, „warte“, „scheiße“, „wie heißt das?“), fliegt komplett raus, auch als Halbsatz. Steht in den Story-Regeln (gilt für Satzauswahl und Nachschnitt); der Nachschnitt darf einen bemängelten Pannen-Satz nur ENTFERNEN, nie „vervollständigen“ (Malo's 06.10.: Nachschnitt machte 87 → 91 s und schlechter). Noch reine KI-Anweisung, an keinem Video bewiesen.
107. **Erst sagen, dann zeigen.** (Inhaber 06.10.: „er sagt ‚jetzt Soße auf den Deckel‘ und dann Cut – man sieht nicht, wie er es macht“; „während er redet, soll man es gleichzeitig sehen“.) Schritt 1 (eingebaut): in Zubereitungs-/Anleitungs-Videos bleiben nach einer Ansage 1,8 s statt 0,8 s der wortlosen Handlung stehen (Folge: Malo's grob +10 s). Schritt 2 (OFFEN, Umbau am Kern): Bild und Ton getrennt führen – Bild der Handlung unter die Ansage legen (nur wo kein Gesicht spricht), Kameraschwenks mit dem Bild danach überdecken, an Clip-Anfängen das Bild kurz vor dem Ton beginnen lassen.
108. **Ton: Stimme vor Hintergrund.** (Inhaber 06.10., Kaiserschmarrn-Umfrage.) Gemessen (Spanne laut/leise): Kaiserschmarrn roh 8 dB, fertig 9 dB – Baguelette fertig 23 dB, Cheesecake 27 dB. Der Lärm steckt in der Aufnahme (kein Mikro nah am Mund), nicht in unserer Kette. Klassische Filter: stärker entrauscht + Gate bringt 14 dB, die Spracherkennung versteht es aber schlechter (Artefakte). Hörproben A/B/C: `material/testlauf/ton-vergleich`. Offen: (a) Variante C nur bei lautem Hintergrund zuschalten – wenn der Inhaber sie besser findet; (b) echte Stimm-Isolierung per lokalem KI-Modell (Download nötig, Zustimmung offen).
109. **Kein fremder Laut am Clip-Wechsel.** (Inhaber 06.10.: vor „Ihr bekommt bei uns“ hört man ein „Ja“.) Die Montage hängt an jeden Satz etwas Nachlauf aus dem Rohclip; darin liegen Zurufe, Klicks, Startsignale. Bisher blieben sie stehen, weil Lücken nur bei echter Stille geschnitten werden. Jetzt endet ein Abschnitt an einer Clip-Grenze in der ersten Ruhe nach seinem letzten Wort, und der nächste beginnt in der letzten Ruhe vor seinem ersten Wort – aber NUR bei kurzen Rändern (≤ 0,8 s), nur wenn dort wirklich ein Geräusch liegt (≥ 8 dB über der Ruhe) und nur an echter Ruhe (≥ 12 dB unter Sprachpegel; sonst könnte es ein verschluckter Wortanfang sein). Geprüft am eingebauten Code: Cheesecake-Rohschnitt −0,77 s (der Laut nach Satz 1 und ein Klick, den die Spracherkennung als „Vielen Dank“ halluziniert), Malo's −1,66 s. Erste, zu breite Fassung hätte bei Malo's 14 s wortlose Handlung entfernt – deshalb die engen Grenzen. Im fertigen Video noch nicht bewiesen. Offen: Der tatsächliche Sprecheinsatz liegt oft bis 0,5 s neben der Zeit der Spracherkennung („Bild setzt zu spät ein“).
110. **Für Ordner-Videos entscheidet die Montage, was man sieht.** (06.10., am Malo's-Rohclip Bild für Bild geprüft.) Regel 107 saß an der falschen Stelle: Die Montage schneidet jeden Satz mit nur 0,6 s Nachlauf aus und teilt an Pausen > 0,9 s – die Handlung nach der Ansage (Soße auf den Deckel: läuft 170–176 s, Ansage 167,4–171,0 s) ist im Rohschnitt gar nicht mehr vorhanden, die Pipeline kann sie nicht zurückholen. Die Schwenks (136–140 s, 144–146 s) liegen in Pausen bzw. unter Pannen-/Nebenbei-Sätzen; „Wir holen es jetzt erst runter“ (141,0–143,0 s) ist mit dem Abkratzen synchron. Wortzeiten immer wortgenau am Ausschnitt holen – Segmentzeiten über lange Pausen lagen bis 7 s daneben. Zielbild: `material/testlauf/BEISPIEL_von-Hand_Malos_Handlung-sichtbar.mp4` (von Hand geschnitten, 32 s). OFFEN: in `montage.py` (a) Nachlauf nach Ansagen bis zum nächsten Wort (Obergrenze, Ton abgesenkt), (b) Schwenk-Messung je Clip, (c) Teilsätze abtrennen („…runter, | dass es nicht verbrannt ist“).
101a. Nachtrag zu Regel 101: Schwelle für „Schreibvariante“ von 0,5 auf 0,72 angehoben – im Cheesecake-Lauf 06.10. rutschte „geraten“ → „gebracht“ (0,67) durch. 18 Fälle am eingebauten Code geprüft. Eine Zeichen-Schwelle bleibt fragil (Baglet/Baguelette 0,75); die robuste Lösung ist weiter die Bestätigung durch die Hörprobe.
111. **Das Bild folgt dem Wort.** (Inhaber 06.10.: „Wenn er sagt ‚zwei Gurken drauf‘, will ich sehen, wie zwei Gurken draufkommen – nicht, wie er in den Behälter greift. Dieses Verständnis muss die Software haben, das ist bei jedem Video individuell.“) Machbarkeit belegt: Die KI (sogar das kleine Modell) fand in zeitgestempelten Kontaktbögen (1 Bild/s) des Malo's-Clips zu vier Sätzen die richtige Bildstelle (Abkratzen 141 s, Gurken 152 s, Patty 162 s, Senf 170 s), meldete beim fünften ehrlich „nicht zu sehen“ und nannte beide Schwenks. Zielbild von Hand gebaut: `material/testlauf/BEISPIEL-2_Bild-folgt-dem-Wort_Malos.mp4` (20,8 s; Ton unverändert, Bild je Satz um 0 / 2,0 / 3,3 / 2,4 s vorgezogen). OFFEN – Einbau in `montage.py`: (a) je Sprech-Clip ohne sprechendes Gesicht Kontaktbögen an die KI, (b) je Satz Bildstelle + Schlüsselwort → Versatz = Bildstelle − Wortzeit, (c) Stück mit getrenntem Bild- und Ton-Fenster schneiden, (d) Satz weglassen oder synchron lassen, wenn die Handlung nicht zu sehen ist, (e) nie über Gesichter, die sprechen. Test braucht das große KI-Modell.

112. **Jedes Element hat seinen eigenen Platz – nichts übereinander, nichts über dem Gesicht, nichts unter den App-Knöpfen**
    (Inhaber 06.10. mit Bildschirmfotos aus TikTok und Instagram). Eine gemeinsame Platzvergabe (`plan_layout`) für Logo,
    Titel, Untertitel, Emojis und Sticker. Freier Rahmen im 1080×1920-Bild: oben ab 240 (Suchleiste/Reiter), unten bis 1400
    (Profil/Beschreibung bei Instagram), links ab 110 und rechts bis 970 (Instagram schneidet auf hohen Handys ~97 px je
    Seite ab), ab Höhe 770 rechts nur bis 870 (Knopfspalte Like/Kommentar/Teilen). Untertitel höchstens 660 px breit, mittig.
    Reihenfolge: Logo oben links (sonst rechts, sonst weg), Untertitel unten (bei Gesicht: unter das Kinn, sonst über den
    Kopf), Titel oben (sonst über/unter dem Gesicht; passt er nirgends, entfällt er), Emoji direkt unter den Untertiteln
    (sonst darüber, sonst neben dem Gesicht, sonst weg). Kurze Einblendungen (Titel, Emoji) prüfen nur die Gesichter, die in
    ihrem Zeitfenster im Bild sind. Getestet an 12.000 durchgespielten Lagen: 0 Überschneidungen.
113. **Gestrichene Zwischenrufe werden wirklich herausgeschnitten** (Fehlersuche 06.10., „Ja“ des Kameramanns). Ein
    gestrichenes Wort, das frei zwischen zwei Pausen steht („Ja.“, „So?“, „Danke.“), blieb hörbar, weil das Stück kürzer als
    die Mindest-Schnittlänge war oder mit den Nachbarn wieder verbunden wurde. Jetzt wird es mit Schutzabstand zu den
    Nachbarwörtern entfernt. Dazu: Zahlwort = Ziffer in der Hörprobe („fünf“ = „5“), sonst löst das ohne Grund den sicheren
    Neuschnitt aus.

114. **Zubereitung im Ordner = wie ein langes Video** (Inhaber 07.10.). Seine Stimme bleibt, das Bild folgt dem Wort, der
    komplette Prozess ist zu sehen, Clips in Dreh-Reihenfolge. Zwischenbilder nie aus Arbeits-Clips, keins über dem Schlusssatz.
    Die stumme schnelle Bildfolge gibt es nur noch per Schalter `VELOX_BILDFOLGE=1`.
115. **Bild und Wort 1:1** (Inhaber 07.10.: „es passt gar nicht … nach der Ansage sieht man die Handlung nicht, sofort Cut“).
    Ein Satz MIT sichtbarer Handlung bekommt das Bild ab kurz vor der Handlung, und die Handlung läuft danach stumm (mit
    Musik) zu Ende – mindestens 1,6 s zu sehen, höchstens 3 s über den Satz hinaus. Ein Satz OHNE eigene Handlung bleibt an
    seiner Stelle bzw. läuft weiter, verbraucht aber nie die Handlung des nächsten Satzes. Nie dasselbe Bild zweimal, nie
    zurückspringen. Fehlstarts und Regie-Sätze („Also, warte …“, „Wie heißt das?“) fliegen vor dem Zusammensetzen raus.

## Regel 116 – Kein Leerlauf, kein Fremdlaut, kein stummer Sprecher als Zwischenbild (Inhaber 08.10., Ordner-Tests Golden Thai)
- **Leerlauf mitten im Abschnitt:** Liegt innerhalb eines Abschnitts 0,4 s oder länger nur Ruhe/Atmen (Pegel unter der Sprech-Schwelle, kein leises Wort darin), bleiben 0,22 s stehen. Grund: Die Worterkennung lässt das nächste Wort oft zu früh beginnen, die Pause sah dann wie Sprache aus („hängt vor ‚Willst du eine Mittagspause‘“, „nach ‚Restaurants‘ kürzer cutten“).
- **Clip-Wechsel:** Der Anfang eines Abschnitts wird auch direkt nach einem Clip-Wechsel am Pegel geprüft (vorher nur nach einer Lücke).
- **Kurzer Laut vor dem Satz** (≤ 0,3 s, danach ≥ 0,3 s Ruhe, noch vor dem ersten Wort): gehört nicht zum Satz und fällt weg.
- **Schlusslaut:** Eine Senke von 0,1 s im letzten Wort ist nicht das Stimm-Ende („Thailan-ds“ bleibt ganz).
- **Zwischenbild (B-Roll) zeigt nie ein Gesicht von vorn** aus einem Sprech-Clip: Über „Nummer 33“ lag eine Aufnahme, in der der Sprecher selbst stumm am Tisch sitzt – man hört ihn und sieht ihn nicht sprechen. Prüfung per Gesichtserkennung (montage.py `zeigt_gesicht`), zusätzlich im KI-Auftrag.
- **Letzte Fassung gewinnt – fest verdrahtet (Kontroll-Läufe 08.10.):** Stehen dieselben 2–6 Wörter zweimal hintereinander und die KI streicht die SPÄTERE Fassung, wird getauscht („Willst du eine – Willst du eine Mittagspause ohne …“). Wird der Anfang der letzten Fassung mitgestrichen („Halal Thai … [Halal] Thai ist kein Trend“), kommt er zurück. Reststücke ohne Wort und ohne Ton (< 0,8 s) fallen weg.
- **Angebot/Aufruf am Schluss** gehört vollständig ins Video (Planungs-Auftrag der Montage) – am Halal-Ordner am 08.10. noch NICHT erreicht, die KI lässt die Einladung weiter weg.
- **Nachtrag 08.10. nachmittags (Inhaber zu Premium/Viral):** (a) Untertitel pausieren NIE für eine Karte – Karten stehen unter/über den Untertiteln oder oben; passt der Stapel nicht, kommen sie einzeln nacheinander, sonst entfällt die Karte. (b) Karte mit Zahl („33 …“) erscheint beim gesprochenen Zahlwort. (c) Ein Schnitt, der < 0,25 s spart und kein gestrichenes Wort entfernt, wird zurückgenommen („mit | Tofu“ klang abgehackt). (d) „X? Nein. X …“ (Anlauf, Korrekturwort, neuer Anlauf): Anlauf und Korrekturwort fallen weg.

## Regel 117 – Ein Ordner = ein Video: alle Clips sind Puzzleteile (Inhaber 08.10., Halal-Ordner)
„Die Software muss wirklich alle Videos aus dem Ordner anschauen und zusammenfügen wie Puzzleteile – fehlt Anfang oder Ende, ergibt das Video keinen Sinn.“
- **Puzzle-Prüfung (montage.py `puzzle`)**: Jeder Sprech-Clip, aus dem kein Satz im Plan steht, wird der KI gezielt vorgelegt: fehlt dem Video ein Anfang, eine Fortsetzung oder der Schluss (Angebot, Einladung, Aufruf)? Der bisherige Plan bleibt unverändert, neue Sätze werden eingefügt. Zweiter Blick je Clip: Gedanke vollständig (jede Aussage in der letzten sauberen Fassung)?
- **Verhörte Strecken neu abhören**: Liefert die Erkennung ≥ 8 Wörter am Stück klein geschrieben und ohne Satzzeichen (Notbetrieb bei sehr leisem Ton), wird die Strecke mit angehobenem Pegel neu abgehört. Ursache des fehlenden Schlusses: 40 s kamen als ein unlesbarer „Satz“.
- **Satzgrenze auch nach ≥ 1,5 s Sprechpause** (Montage).
- Premium/Feinschnitt: Laut-Fetzen (eine Messstelle, ≥ 8 dB unter Sprache) in einer Pause wird mit der Pause herausgeschnitten („mit – [m] – Tofu“).

## Regel 118 – Aus mehreren Versuchen die richtige Fassung (Inhaber 08.10. abends)
„‚Mein Angebot an dich‘ bei Sekunde 14 war nur die Vorbereitung – später kommt derselbe Satz noch einmal, in besser.“
- **Letzte Fassung gewinnt (montage.py `letzte_fassung`)**: Kommt im selben Clip später ein Satz, der die gewählten Sätze noch einmal enthält und im Wesentlichen aus ihnen besteht (≥ 55 %, also kein Gespräch ÜBER den Satz), ersetzt er sie – auch mehrere Teilsätze durch eine zusammenhängende spätere Fassung.
- **Anlauf kappen**: Beginnt die gute Fassung selbst mit „X, nein, X …“, fällt nur der Anlauf weg, nicht der Satz. Ein Satz mit ≥ 8 Wörtern wird nie als „Fehlstart“ ganz gestrichen.
- **Leise Strecken**: Neu-Abhören in bis zu 5 Versuchen (Ränder, Pegel, Satzzeichen-Vorgabe).

## Regel 119 – Ton und Wortanfänge (Inhaber 08.10. nachts)
- **Ton-Kette**: Arbeitspegel → leichte Entrauschung (afftdn 6 dB) → sein Broadcast-Preset (Multiband-Kompressor, Werte aus `material/preset/Broadcast Preset.prfpset`) → Begrenzer → Lautheit −14. KEIN Expander/Gate: seine Hörprobe damit war „abgehackt“.
- **Sehr leise Aufnahmen** (Sprache < −33 dB): Die Erkennung im Feinschnitt hört auf einer lauter gedrehten Kopie; Messungen bleiben auf dem Original.
- **Wortanfang am Schnitt**: mindestens ~0,1 s Vorlauf vor dem Stimmeinsatz, Abschnitt beginnt notfalls früher (Anlaut „E“ in „Echtes“ ging verloren).
- **KI darf kein einzelnes Inhaltswort** zwischen zwei behaltenen Wörtern streichen („Mittagspause [ohne] Foodkoma“).
- **Nachschnitt**: „doppelt“ nur, wenn EIN anderer Satz ≥ 60 % desselben Inhalts hat (nicht über alle Sätze zusammengezählt). Teilstück eines Satzes ist nie „Fehlstart“.

## Regel 120 – Zubereitung „wie mein Schnitt“ (Entscheidung des Inhabers 08.10. nachts)
Nach dem Malo's-Test mit durchgehender Erzählung (82 s, Satzfetzen): „Mach wie mein Schnitt.“
- Begrüßung gesprochen → Zubereitung als **stumme schnelle Bildfolge** mit Musik (alle Schritte, Dreh-Reihenfolge) → Schluss gesprochen. Normalfall, sobald vor/nach der Zubereitung jemand in die Kamera spricht (abschalten nur mit `VELOX_BILDFOLGE=0`). Ersetzt die Entscheidung vom 07.10. („Erzählung bleibt“).
- **Zeitbudget Bildfolge 14 s**: je Schritt 14 s / Anzahl, mindestens 0,55 s, höchstens 1,2 s.
- **Schlussteil höchstens ~12 s**: Fertig-Meldung, höchstens eine Besonderheit, die letzten ein bis zwei Sätze (KI-Auswahl, Rückfall fest).
- **Puzzle-Prüfung** (Regel 117) nur bei Plänen unter 40 s Sprache.
- Ergebnis Malo's Junior Box: 28,7 s, dieselben vier Sätze wie sein Schnitt (27,9 s).

## Regel 121 – Gleichlauf, Bildruhe, Musik (Inhaber 09.10.)
- **Gleichlauf Bild/Ton/Untertitel**: Teilstücke der Montage sind bildgenau (ganze Bilder), Ton unkomprimiert und exakt gleich lang; AAC wird nur EINMAL am Ende erzeugt (vorher lief der Ton je Teilstück 20–40 ms nach, bei 25 Stücken ~0,5 s). Kontrolle nach jedem Zusammensetzen im Protokoll („Gleichlauf Rohschnitt“). Die Ton-Kette (Broadcast + Entrauschung) verzögert um 30 ms – wird ausgeglichen.
- **Pegel-Messungen ohne digitale Stille**: Stumme Bildfolgen (−120 dB) zählen bei „Ruhe“/„Sprachlautstärke“ nicht mit (sonst wird am Satzanfang nichts gekürzt).
- **Bildruhe gemessen, nicht geschätzt**: Jeder Clip wird an sechs Stellen vermessen (Kamerabewegung, Schärfe) → „verwackelt/unscharf (gemessen)“ steht in der Planung; solche Clips liefern keinen Satz, wenn derselbe Inhalt woanders liegt, und kein Zwischenbild. Jedes stumme Bild-Stück wird auf das ruhigste, schärfste Fenster in ±0,7 s gelegt; ist auch das verwackelt/unscharf, entfällt es.
- **Zubereitung**: Regie-Ansagen werden kein Bild; zeigt der Satz nach der Bildfolge das fertige Produkt, entfällt das Hochhalten am Ende der Bildfolge; gestrichene Schlussteil-Sätze mit Handlung bleiben als kurze stumme Bilder (je 0,9 s, höchstens 5).
- **Musik**: Entscheidung nach Regeln (modern; passend zum Unternehmen; landestypisch nur bei klarer Landesküche, nie das falsche Land; passend zur Videoart; im Zweifel modern-neutral). Eingebrannt wird Musik nur bei stummen Strecken oder wenn der Kunde sie wählt – sonst nur Empfehlung. Neue Stücke: mp3 in `musik/` + Eintrag in `musik/katalog.json`. Nie TikTok-/Chart-Musik einbrennen.

## Regel 122 – Selbstkontrolle bis zur Auslieferung (Inhaber 09./10.10.: „Das Endvideo muss wirklich fehlerfrei sein“, ohne dass er freigeben muss)
Reihenfolge je Video: Clips messen → Bild-Stücke prüfen (Bildruhe, Moment ohne fremde Hand, Endkontrolle im exakten Fenster) → Schnittstellen im Bild prüfen (Rand ≤ 0,6 s kürzen, nie ins Wort; am Videoende 0,35 s Ausklang) → schneiden → Abnahme + Sinn-Check (mit Nachschnitt) → **zweiter Schnitt** (fertigen Ton nachmessen: Leerstellen 0,5–1,5 s kürzen, Splitter weg) → **Schluss-Abnahme** des auszuliefernden Videos.
- Schluss-Abnahme als Korrektur-Schleife (Inhaber 10.10.: keine Nachricht an ihn, kein Warten auf Freigabe; „nur die Sachen verbessern, die nicht passen“): bestanden nur mit Note ≥ 8 (`VELOX_ABNAHME_NOTE`) UND ohne Schnittfehler UND ohne offene Untertitel-Korrektur. Sonst `gezielt_reparieren` – nur die bemängelte Schnittstelle (abgehackt → mehr Luft, nie in ein gestrichenes Wort oder über einen Clip-Wechsel; Pause → bis ans Wort kürzen, nie in stummen Strecken; Doppeltes/Panne → nur diese Wörter heraus), der Rest bleibt Bild für Bild. Dann neu einbrennen und wieder abnehmen, bis zu 3 Runden (`VELOX_ABNAHME_RUNDEN`). Die beste Fassung bleibt liegen; wird eine Runde nicht besser, gilt wieder die beste. Letzter Versuch: vorsichtige Sicherheits-Fassung (`KONSERVATIV`). Ausgeliefert wird immer die beste abgenommene Fassung; `pruefung.bestanden` und der Verlauf stehen im Ergebnis.
- Selbstkontrollen: geplante stumme Strecken müssen im Schnitt sein (`zeigen_sichern`); Gleichlauf Rohschnitt; ein doppelter Satzanfang behält genau eine Fassung.
- Nachhören verschluckter Wörter (`recover_swallowed`) erfindet nichts mehr: nur wo wirklich gesprochen wird, keine Doppelung der Wörter davor, nichts hinter dem gesuchten Wort. (Ursache des tagelangen „Willst du eine“-Fehlers; danach 3 von 3 Läufen identisch richtig.)
- Bindestrich-Wörter sind im Text UND in der Hörprobe ein Wort; am Videoende zählt leiser Ausklang als Stimme (0,40 s); gehörte Wörter ohne Untertitel werden nachgetragen, wenn sie nicht zu ≥ 1/3 überdeckt sind.

## Regel 123 – DaVinci Resolve als Farb-Stufe, Farbe je Aufnahme (Inhaber 12.10.: „die Software mit DaVinci verbinden“)
- Mit `VELOX_RESOLVE=1` (und nur wenn Resolve offen ist, Brücke angeklickt) rechnet `render_cut` ohne ffmpeg-Farbe; der fertige Schnitt läuft durch Resolve (`worker/resolve.py`). Scheitert irgendetwas, gibt es die gewohnte ffmpeg-Farbe – ein Auftrag scheitert nie an Resolve. Ist Resolve belegt, wird bis 150 s gewartet.
- Gemessene Grundkorrektur als CDL, jeder Wert begrenzt: Weißpunkt nur 60 % des Weges und höchstens +18 % (dunkel gedrehtes Restaurant bleibt stimmungsvoll), Helligkeit am GESICHT gemessen (Haut 0,40–0,60 bleibt, wie sie ist; ein schwarzes Hemd in der Bildmitte hatte das Bild zu hell gemacht), Weißabgleich nur bei ≥ 5 % farbloser Fläche und höchstens 4 %.
- Video aus mehreren Rohclips: jede Aufnahme (Clip-Wechsel aus dem Schnittplan) bekommt ihre eigene Korrektur; gleich aussehende Stücke werden zusammen gemessen. Zwischenbilder (B-Roll) gehen ebenfalls durch Resolve.

## Regel 124 – Hörprobe: gleiche Wörter erkennen, Untertitel sauber (Testläufe 12.10.)
- Kein „fehlendes Wort“ mehr bei: „gibt's“/„gibt es“, anders gebeugt oder geschrieben („einen“/„eine“, „Malus“/„Malos“, Ähnlichkeit ≥ 0,75 in der Lücke), Umlaut („Burger“/„Bürger“), zusammengesetzt („Junior Burger“/„Junior-Burger“/„Juniorburger“). Vorher löste das grundlos den groben Neuschnitt im sicheren Modus aus (Golden Thai Halal: 2,5 → 0 Fehler).
- Bindestrich-Wörter werden Teil für Teil verglichen; ein gehörter Wortteil wird nicht mehr als „fehlender Untertitel“ nachgetragen („Junior Junior-Box“).
- Namen & Marken des Kunden stehen im ganzen Video gleich („Malus“/„Marlos“ → „Malo's“; nur Namen ab 5 Buchstaben, gleicher Anfangsbuchstabe, Ähnlichkeit ≥ 0,8).
- Satzanfang im Untertitel immer groß (auch wenn das Wort davor herausgeschnitten wurde).

## Regel 125 – Nichts überlappt (Inhaber 12.10., zum zweiten Mal): Karten nie auf Untertiteln
- Stichwort-Karten: JEDER mögliche Platz wird gegen die Untertitel-Zeile geprüft (mind. 10 px Abstand), auch der Platz „oben“. Kein freier Platz → keine Karte. Weiter gilt: nichts auf dem Gesicht, nichts in den Randbereichen von TikTok/Instagram (Profil, Likes, Beschreibung).

## Regel 126 – Anlauf-Wort am Anfang einer Aufnahme (Inhaber 12.10., Cheesecake „unter 10 Euro“: „Er sagt ‚So.‘ – erst ab ‚Ihr bekommt‘“)
- Der Anfang jeder Aufnahme (Clip-Wechsel) wird einzeln neu abgehört (`take_anlauf`). Steht vor dem ersten Wort des Satzes ein Anlauf-Wort („So“, „Ja“, „Okay“, „Also“ …), das nicht im Text steht, beginnt der Schnitt erst danach – an der leisesten Stelle dazwischen. Nur wenn das Wort danach zum Text passt.

## Regel 127 – Einblendungen prüfen sich gegenseitig (Nacht 12./13.10., zu Regel 125)
- Gemeinsames Register `BELEGT` je Clip: Karten haben Vorrang; Logo, Sticker und Emoji entfallen, wenn an ihrer Stelle zur selben Zeit schon etwas steht (12 px Rand). Vorher prüfte jede Einblendung nur gegen Gesicht, Titel und Untertitel.
- Eine Karte endet auch, wenn ein Zwischenbild (B-Roll) kommt oder geht – es ist ein Bildwechsel wie ein Schnitt.
- Karten nur für echte Stichwörter: Hauptwort/Name (groß), Zahl oder mehrere Wörter. Einzelne klein geschriebene Wörter („frisch“, „ein“, „verlosen“, „folgen“) bekommen keine Karte.

## Regel 128 – Anfang und Ende einer Aufnahme, Satzreste (Nacht 12./13.10., zu Regel 126)
- `take_anlauf` prüft den ERSTEN Abschnitt nach jedem Clip-Wechsel (er beginnt oft 0,3–0,8 s hinter dem Wechsel – die erste Fassung mit 0,3 s Toleranz griff nie). Protokoll je Aufnahme: was gehört wurde, womit der Text beginnt.
- Gegenstück am Ende jeder Aufnahme: Folgen nach dem letzten Satzwort nur Regie-/Abschlusswörter („Ja“, „Okay“, „nochmal“, „passt“ …, deutlich gehört), endet der Schnitt davor. Andere Wörter bleiben immer stehen – sie können ein überhörtes Satzende sein.
- Ein Video beginnt nie mit einem Satzrest: Sind die ersten ein, zwei Wörter ein eigener „Satz“ mit Punkt (keine Begrüßung, kein Ausruf, keine Frage), fällt er weg (`kurz_anfang`; Erdbeerbecher begann mit „More.“).
- Montage: Endet der Satz vor einem gewählten Satz im selben Clip ohne Satzzeichen, gehört er dazu und wird mitgenommen (höchstens 2 Teile, 12 Wörter).

## Regel 129 – Umfragen mit mehreren Sprechern, Füll-Laute, Preise (Nacht 13.10., Testläufe neu2/runde3)
- Preise, die die Erkennung zerlegt („1“ „,99..“), sind EIN Wort „1,99.“ (höchstens ein Satzzeichen am Ende).
- Zweiter Schnitt hört den FERTIGEN Ton zweimal auf Füll-Laute ab (normal und mit Füll-Laut-Vorgabe). Ein „äh/ähm“, das nicht auf einem Untertitel-Wort liegt und wirklich Stimme hat, wird herausgenommen. Ein Füll-Laut wird nie als Untertitel nachgetragen.
- Sicherer Neuschnitt nur noch, wenn nicht gehörte Wörter AM SCHNITT liegen (≤ 0,3 s vom Rand eines Abschnitts). Nicht gehörte Wörter mitten im Abschnitt sind Verhörer (Passanten: „Hallo“, „Ja.“, „Natürlich.“) – kein Schnittschaden. Vorher machte der grobe Neuschnitt gestrichene Wörter wieder hörbar (doppeltes „Okay“).
- Ein Video endet nie mit einer Begrüßung („… Tschüss. Hallo.“ – das Hallo gehört zum nächsten Gespräch); wird wiederholt geprüft.
- Ordner-Videos: Die KI darf beim Feinschnitt keinen GANZEN geplanten Satz (ab 4 Wörtern) streichen, außer er kommt doppelt vor (Malo's verlor in einem Lauf „Und jetzt ist unser Junior Burger fertig.“).
- Resolve-Farbe wird erst beim endgültigen Schnitt gerechnet (`RESOLVE_OFFEN`), nicht bei jedem Zwischenschnitt (vorher bis zu dreimal je Auftrag).

## Regel 130 – Gegenprobe, Wortendungen, Einzelwort-Rest (Nacht 13.10., Runden 4–5)
- Vor dem groben Neuschnitt (sicherer Modus) werden die fraglichen Wörter im ROHMATERIAL nachgehört. Versteht die Hörprobe ein Wort auch dort nicht, ist es ein Verhörer und zählt nicht. Nur Wörter, die im Rohmaterial zu hören sind und im Schnitt nicht, gelten als Schnittschaden.
- Das Lektorat ändert keine Wortendungen (s, n, e, en, es): „fünf Classic Cheesecakes“ bleibt Mehrzahl, auch wenn der Kunden-Name „Cheesecake“ heißt.
- Bleibt von einem Satzteil (bis Komma/Punkt, mind. 3 Wörter) nur ein einziges Wort übrig und ist sein Ende gestrichen („…anders, nicht [immer das Gleiche.]“, „Ich [höre, ja.]“), fällt das Wort mit weg.

## Regel 131 – Nichts Fremdes im Video (Inhaber 13.10.: „Teilvideos, die gar nicht zu dem Video gehören“ / „immer die gleichen Fehler“)
- Ursache gefunden: Die Puzzle-Prüfung (08.10., „es fehlt ein Stück“) hängte nachträglich Sätze aus ungenutzten Clips an – auch von anderen Personen an anderen Orten (Umfrage mit zwei Frauen auf der Straße + Werbesatz vom Mann hinter der Theke). Danach prüfte niemand mehr.
- `fremde_teile()` in montage.py läuft nach dem Schnittplan UND als letzte Kontrolle nach der Puzzle-Prüfung: (1) fest – nach einem Abschied kommt nichts mehr aus einem anderen Clip; (2) eigene KI-Frage nur dazu, welcher verwendete Clip ein anderes Thema/Gespräch ist; dessen Sätze fallen weg, solange ≥ 2 Sätze und ≥ 55 % der Wörter bleiben.
- Grundsatz ab jetzt: Eine Regel, die nur als Satz im KI-Auftrag steht, gilt als NICHT umgesetzt. Jede Regel braucht eine Kontrolle im Code, die das Ergebnis prüft.

## Regel 132 – Das richtige Ende (Inhaber 13.10.: „endet viel zu spät oder zu früh – das musst du selber einschätzen können“)
- Zu früh: Der letzte Satz läuft immer bis zu SEINEM Satzende; gestrichene Wörter bis dahin (höchstens 6, 3,5 s) kommen zurück (Golden Thai Veggie endete mitten im Namen „Pad Pak Ruam [Mit, mit Tofu.]“, weil „mit Tofu“ als Doppelung galt).
- Zu spät: `schluss_pruefung()` – eine eigene KI-Frage nur dazu, nach welchem Satz das Video zu Ende ist. Was danach kommt, fällt weg (höchstens die letzten 3 Sätze, höchstens 30 % des Videos).

## Regel 133 – Look je Kunde (Inhaber 13.10.: „alle Videos sehen gleich aus – auf das Restaurant oder Unternehmen anpassen“)
- `--color auto` / `--caption auto`: Die kräftigen Farben im Logo und im Material werden gemessen; die KI wählt daraus die Markenfarbe (sonst eine zur Branche), `lesbar()` bringt sie auf gute Lesbarkeit. Der Untertitel-Stil kommt aus den 11 vorhandenen, passend zu Branche, Tonfall und Schnitt-Stil. Karten, Titel und Premium-Thema übernehmen die Farbe.
- Webseite: Haken „Look automatisch an dein Video anpassen“ (Standard an) schickt `auto`; ohne Haken wählt der Kunde wie bisher selbst. Im Code fertig, live erst nach `zsh scripts/deploy.sh` (Inhaber).

## Regel 134 – Was der Handschnitt besser macht (13.10., Vergleich mit dem von Claude direkt geschnittenen Eröffnungs-Video)
- Vorbild: `schnitt-maschine/test/handschnitt/eroeffnung/edit/` (bauen.py, final.mp4) und das Video des Kollegen. Unterschiede zur Software: Aufhänger zuerst, Produkt-Nahaufnahme genau beim genannten Wort, Schluss-Montage im Takt mit Schild, kleine Untertitel mit groß gesetztem Schlüsselwort, Einblendungen zum Inhalt (Preiskarte, Ortskarte).
- `aufhaenger()` in montage.py: eigene KI-Frage nur zur Reihenfolge – der stärkste, allein verständliche Satz kommt nach vorn (nicht bei Zubereitung/Umfrage; nie ein Satz, der mit „Und/Oder/Natürlich auch/Wie zum Beispiel“ beginnt).
- Untertitel-Stil `akzent`: kleine weiße Zeile, Wort für Wort; das Schlüsselwort am Ende eines Satzteils (≥ 6 Buchstaben, höchstens alle 1,8 s) steht allein, groß und kursiv. Über `--caption auto` wählbar.
- Ein fairer Ordner-Test braucht den GANZEN Ordner des Kunden (Sprech-Clips UND Produktbilder) – Test-Ordner nur aus Sprech-Clips können keine Zwischenbilder zeigen.
- Offen (Entscheidung des Inhabers): Schluss-Montage aus Produktbildern für Sprech-Videos – braucht Musik, die bei Sprech-Videos bisher standardmäßig AUS ist.

## Regel 135 – Claude-Schnitt: jede Fassung so abhören, wie der Zuschauer sie hört (13.10., Papaya-Video aus unbekanntem Material)
- Blick: Vor der Wahl einer Fassung ein Bild daraus ansehen – schaut die Person in die Kamera oder liest sie vom Handy ab? Nur Fassungen mit Blick in die Kamera.
- Verständlichkeit: Nicht die flüssigste, sondern die am klarsten gesprochene Fassung nehmen. `bauen` hört jeden geschnittenen Sprechteil einzeln ab (ohne Zusammenhang) und warnt unter 0,85 („⚠ UNDEUTLICH“). Bei Warnung: Gehörtes mit dem Plan vergleichen – steht dort ein Wort mehr oder ein anderes, stimmt der Schnitt nicht.
- Verschluckte Wörter: Eine Pause ≥ 0,7 s mitten im Satz, in der es nicht still ist, enthält oft Wörter, die die Erkennung ausgelassen hat („…die eine Frucht, [die in 24 Stunden] deine Verdauung…“). `hoeren` hört solche Lücken jetzt von selbst nach. Trotzdem gilt: ergibt ein Satz im Transkript grammatisch keinen Sinn, die Stelle einzeln abhören, bevor geschnitten wird.
- Ein Satz bleibt ein Sprechteil: Zwei Sprechteile, die im Rohclip direkt hintereinander liegen, werden als EINER geplant – sonst fällt weg, was dazwischen gesprochen wurde.

## Regel 136 – Claude-Schnitt: drei Stufen, die man unterscheidet – und eine Grundqualität, die alle haben (Inhaber 13.10.)
- JEDE Stufe: letzte saubere Fassung, Schnittkanten in der Ruhe, Farbe je Rohclip gemessen (`farbe()`), Stabilisierung wo das Bild zittert (`stabilisieren()`), Untertitel neben dem Gesicht, Ausschnitt-Wechsel am Schnitt innerhalb derselben Aufnahme, −14 LUFS.
- `"stufe": "clean"` – ruhig: kleine weiße Untertitel, Schlagwort kursiv (88), Wechsel 6 %, keine Karten/Emojis (nur „ort“ am Schluss), keine Töne.
- `"stufe": "viral"` – Wechsel 12 %, Heranfahren im Aufhänger, Glas-Karten + Emojis, Töne, Schlagwort groß (112) in Akzentfarbe.
- `"stufe": "premium"` – wie viral, dazu Heranfahren in jedem Sprechteil, Kino-Farbe mit leichter Randabdunklung, Fortschrittslinie oben in Akzentfarbe.
- Akzentfarbe nur, wo sie lesbar ist: Das Werkzeug misst die Fläche hinter dem Untertitel; ist die Farbe ihr zu ähnlich (Gold auf beigem Hemd), bleibt das Schlagwort weiß.
- Stabilisierung wird nachgemessen: Bringt sie nicht mindestens 30 % weniger Zittern (wilde Handy-Schwenks), bleibt das Original. Stativ-Aufnahmen werden nie angefasst.
- `"logo": <Pfad>` – Kundenlogo klein oben links. `"musik"` – Name aus `schnitt-maschine/musik/` oder voller Pfad aus `material/MUSIK/<Kategorie>/`.
