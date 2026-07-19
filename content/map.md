---
title: "QSO Map"
description: "Every station N0YEP has worked, plotted on a world map."
---

Every contact in the log, plotted from its grid square or reported coordinates.
Home is marked at the crosshair; dot size scales with how many QSOs came from
that spot. Hover a dot for the callsign, count, and bands.

{{< qso-map >}}

Rebuild this map after importing a new log:

```bash
python3 scripts/build-qso-map.py n0yep-logbook.adi --home EN33mq
```
