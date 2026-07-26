# UPnP WAN byte-counter spike

This is an operator-run v0.1 spike. It deliberately does not run as part of the
application or test suite and has not been run against the live network.

Save the following as `spike_upnp.py` and run it from the netmon host:

```python
import socket

MESSAGE = "\\r\\n".join([
    "M-SEARCH * HTTP/1.1",
    "HOST: 239.255.255.250:1900",
    "MAN: \"ssdp:discover\"",
    "MX: 3",
    "ST: urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1",
    "",
    "",
]).encode()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.settimeout(5)
sock.sendto(MESSAGE, ("239.255.255.250", 1900))
try:
    while True:
        payload, address = sock.recvfrom(65535)
        print(f"--- response from {address} ---")
        print(payload.decode(errors="replace"))
except socket.timeout:
    print("Discovery window closed")
finally:
    sock.close()
```

## How to interpret the result

Look for a response whose `ST` or `USN` identifies the requested
`WANCommonInterfaceConfig:1` service. Follow its `LOCATION` URL in a browser or
HTTP client and inspect the device description for the control URL. A successful
SOAP `GetTotalBytesSent` and `GetTotalBytesReceived` call would establish that
the v0.4 WAN approach is available. A response for only an unrelated device or
no response means the router does not expose the required service to this host.

## Finding

_Operator: record the date, router model/firmware, response, and whether both byte
counter actions succeeded here._
