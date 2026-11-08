# Konstante Leistung: beste Widerstandsstufe (Christopeit AX 4000)

Bei konstanter Leistung und konstanter Trittfrequenz spielt die Steigung keine Rolle: Es gibt **eine Stufe für die ganze Fahrt**. Sie hängt nur von Zielleistung und Trittfrequenz ab. Leistungen aus der Watt-Tabelle des Handbuchs (über 80 rpm mit rpm hoch 1,7 hochgerechnet).

## Beste Stufe bei 80 rpm

| Zielleistung | Stufe | Watt laut Tabelle |
|---:|---:|---:|
| 110 W | 5 | 110 |
| 125 W | 6 | 125 |
| 140 W | 7 | 142 |
| **160 W** | **8** | **159** |
| 175 W | 9 | 176 |
| 190 W | 10 | 187 |
| 200 W | 11 | 203 |
| 220 W | 12 | 220 |
| 235 W | 13 | 236 |
| 250 W | 14 | 252 |

- Gewohnte Grundlagenfahrt (≈ 160 W): **Stufe 8 bei 80 rpm**.
- 200-W-Ziel aus dem Trainingsplan: **Stufe 11 bei 80 rpm**.

## Hinweise

1. **Trittfrequenz konstant halten.** Die Stufe stimmt nur bei dieser Drehzahl: Auf Stufe 8 ergeben 85 statt 80 rpm schon ≈ 175 W statt 159 W. Wer lieber 90 rpm fährt, braucht für 160 W Stufe 6–7.
2. **Welche Drehzahl.** Bei 150–200 W ist 60–80 rpm energetisch am sparsamsten; 80–90 rpm schont die Muskulatur, weil jeder Tritt weniger Kraft braucht. 80 rpm ist ein guter Kompromiss.
3. **Automatisch regeln.** `ergometer.py --power 160` hält die Leistung selbst: Schneller treten nimmt Widerstand weg, langsamer treten legt Widerstand drauf. Die Steigungssimulation in Kinomap dabei ausschalten.
4. **Training gegen Wettkampf.** Für das Training ist konstante Leistung optimal: gleichmäßiger Puls, genau im gewünschten Bereich. Für die schnellste Zeit auf einer echten Strecke wäre bergauf 5–10 % mehr und bergab weniger etwas besser, weil man am Berg langsam fährt und dort jedes zusätzliche Watt am meisten Zeit spart. Am Ergometer bringt das nichts.
