# Refreshing the bundled OUI snapshot

netmon intentionally does not download vendor data at runtime. To refresh the
trimmed dataset, download the current IEEE OUI registry from
<https://standards-oui.ieee.org/oui/oui.txt>, select the prefixes needed for the
common vendors in the local network, and write them to
`src/netmon/data/oui.csv` as:

```text
001A11, Google
B827EB, Raspberry Pi Foundation
```

Prefixes may be written with separators in the source registry; normalize them
to six uppercase hexadecimal characters before committing. Keep the file
trimmed, run `uv run pytest`, and verify the resulting wheel contains
`netmon/data/oui.csv` with `uv build`.
