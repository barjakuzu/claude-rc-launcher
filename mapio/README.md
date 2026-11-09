# Mapio

A touch-first browser map for the Tesla in-car browser (works on any browser).
Pure static site — no build, no API keys.

- Dark / light / streets / satellite maps (CARTO, OSM, Esri)
- Live location + follow mode, speed in km/h
- Place search (Nominatim), long-press the map to drop a pin
- Driving directions with a turn list and next-maneuver banner (OSRM)

## Run with a free Cloudflare link

```bash
cd mapio
./serve.sh        # prints https://<random>.trycloudflare.com
```

Open that link in the car's browser. Geolocation needs HTTPS, which the tunnel provides.
The link changes each time `serve.sh` restarts and only works while your computer is on.
