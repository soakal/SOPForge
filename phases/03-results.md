## AC3: sopforge-server.exe cold-start timing and clean exit

- First launch after build: 6.614s (one-time AV-scan cost, same mechanism as Phase 1's sopforge.exe — see phases/DEVIATIONS.md)
- Steady-state launches (3 repeats): 3.766s, 3.708s, 3.850s (average 3.775s, threshold 5.0s)
- Clean exit return codes: [0, 0, 0, 0]

