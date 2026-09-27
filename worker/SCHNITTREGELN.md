# Velox Cut – Schnittregeln

Diese Datei ist das „Gedächtnis“ der Schnitt-Pipeline (`worker/pipeline.py`). Jede Rückmeldung zu einem geschnittenen
Video wird hier als feste Regel festgehalten und im Code umgesetzt – so wird jeder Schnitt besser als der vorige.
Protokoll aller Schnitte: `/Volumes/PortableSSD/velox-worker/logs/jobs.jsonl` (was entfernt wurde, Probleme, Gemini an/aus).

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

## Stile
| | Clean | Viral |
|---|---|---|
| Zoom | sanfter Wechsel (1,07) an Sprungschnitten | Punches 1,0 / 1,16 / 1,06 / 1,22 |
| Titel | – | Hook-Titel die ersten 3 s |
| Noch offen | – | Sounds (Whoosh/Pop), Emojis, Hervorhebung wichtiger Wörter |

## Rückmeldungen & daraus entstandene Regeln
| Datum | Video | Rückmeldung | Regel / Änderung |
|---|---|---|---|
| 27.09.2026 | Nunu-Selfie (52 s, dunkel) | Rohmaterial kam ungeschnitten raus, keine Untertitel | provisorische Pipeline ersetzt; Qualitäts-Check eingebaut |
| 27.09.2026 | dto. | Untertitel zu klein, lagen auf dem Kinn | Größen wie Website-Vorschau, Position weicht Gesicht aus |
| 27.09.2026 | dto. | Video zu dunkel | automatische Aufhellung nach gemessener Helligkeit |
| 28.09.2026 | dto. | Test: Wortenden abgeschnitten („Novosmir“, „dabei“ fehlte, „Durchziele“) | Regeln 7, 8, 11 |
| 28.09.2026 | Kaiserschmarrn-Straßeninterview (Kollege) | Start mit Vorbereitung/„nochmal von vorne“, Ende mit „okay super, passt?“ zum Kameramann, Übergänge abgehackt, Untertitel sollen genau passen und nicht springen | Regeln 12, 13, 14 – Ergebnis: Szene startet bei „Hallo, wollt ihr …“, endet bei „Tschüss“, 4 weiche Schnitte |
| 28.09.2026 | dto. | Test: „Ähm“ nach „Stand jetzt“ blieb hörbar, gedehntes „aaaber“/„uuund“ | Regeln 9, 10 – Ergebnis: 51,4 → 44,8 s, keine Füllwörter, alle Wörter da |
