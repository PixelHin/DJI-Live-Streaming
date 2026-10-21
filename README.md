# DJI Live Streaming

Windows, Python 3, and Java are required. Python must be available as `python`, and Java as `java`.

## Start the server

1. Open the project folder.
2. Double-click `start_server.bat`.
3. Leave that window open. It starts Redis, the media server, the SIP server, and the website.
4. Open a browser at `http://127.0.0.1:8765`.

Type `stop` in the server window to exit.

The GB28181 page is `http://127.0.0.1:8765/gb28181.html`. In FlightHub 2 and on that page, use SIP server port `8116`, SIP server domain `4101050000`, SIP server ID `41010500002000000001`, and password `12345678`. The SIP server IP is this PC’s address that the dock can reach. Forward UDP and TCP `8116` to this PC.

## API Call

On the website, press **API Call**. Copy these four values from FlightHub 2 into the page, then press **Play**.

| Website field | Copy from FlightHub 2 |
| --- | --- |
| Device SN | The dock or aircraft serial number on the device page |
| Camera index | The camera index for that device. The dock camera used here is `165-0-7`, which is already filled in |
| Project UUID | The project UUID (`X-Project-Uuid`) |
| User token | The OpenAPI user token (`X-User-Token`) |

FlightHub chooses the playback provider. The page plays Agora, Volcengine, or WebRTC when FlightHub returns one of those.

## Stream Forwarding

On the website, press **Stream Forwarding**, then **RTMP**.

1. On the router, forward TCP port `1935` to this PC’s LAN address. Find that address with `ipconfig`.
2. Allow inbound TCP `1935` in Windows Firewall.
3. In the FlightHub 2 RTMP channel, set the address to this PC’s public IP and the stream path:

```text
rtmp://<public-ip>:1935/live/test
```

4. On the website, type the same path on this PC:

```text
rtmp://127.0.0.1:1935/live/test
```

5. Leave **Device SN** empty.
6. Turn the FlightHub channel on. When the server window logs an RTMP connection, press **Play**.

`live` is the application name and `test` is the stream name. FlightHub and the website must use that same path.

For RTSP, press **RTSP** and paste the RTSP link. A stream pushed to this PC uses `rtsp://127.0.0.1:554/live/test` on the website and `rtsp://<public-ip>:554/live/test` in FlightHub. Forward TCP `554` for that push. If the RTSP password contains `#`, `!`, or `^`, encode them in the link as `%23`, `%21`, and `%5E`.
