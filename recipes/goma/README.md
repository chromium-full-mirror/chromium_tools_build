# recipes/goma

This directory contains goma-related protobuf to be uploaded with
`infra.libs.bigquery.helper.send_rows`.

## How to update `*_pb2.py` files?

1. Download [goma\_stats.proto](https://chromium.googlesource.com/infra/goma/client/+/main/lib/goma_stats.proto).
1. Install the latest [protobuf library](https://github.com/google/protobuf).
1. Execute following command to generate pb2.py files, and check them in.
```
 $ protoc --python_out=. compile_events.proto goma_stats.proto
```
