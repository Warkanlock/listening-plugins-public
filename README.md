# Listening Plugins

## Bring your own music sources.

[Listening Room](https://github.com/Warkanlock/listening-public) builds a library from the music files you own. An optional plugin can search a catalog, retrieve audio you have permission to store, or do both. Listening verifies a recording before adding it to the library.

## Install a plugin

On a Linux Docker host, give the Listening engine manager a verified copy of the publisher's public signing key. For a public release, point the manager at this repository and pin the desired release tag. For a private release, download its signed catalog files using your GitHub session and give the manager the local catalog path. If the images are private too, sign Docker in to the image registry as the host user running the manager. Open **Settings → Extensions**, install the plugin, enter the settings it requests, then test and enable it.

[Engine manager setup](https://github.com/Warkanlock/listening-public/blob/main/docs/download-engines.md) · [Plugin protocol](https://github.com/Warkanlock/listening-public/blob/main/examples/engines/README.md)

| Plugin | Source | Settings |
| --- | --- | --- |
| Example catalog | An HTTP or HTTPS catalog you operate | Catalog URL |
| Sockseek | A separately distributed search and transfer tool on Linux amd64 | Account name and password |

The example catalog matches artist and title against `catalog.json` and retrieves files under `audio/`. Its catalog is a JSON list of records such as `{"id":"first-light","artist":"Example Artist","title":"First Light","formats":[".mp3"]}`. The same catalog can be used as a local development fixture.

For an existing Sockseek installation, the plugin also reads the operator's existing `sockseek.conf` if it is staged in this plugin's credentials directory. The readiness check verifies configuration and the executable and reports a recent failed login. The first search signs in to Soulseek. On managers that start workers with Docker init, search and download reuse that connection inside the worker. Older managers use a foreground process per operation. An upstream login outage keeps the request queued while the worker waits before retrying. Test an authorized recording before moving an existing room's jobs to this plugin.

## Build a plugin

Create `plugins/<id>/manifest.json` and `plugins/<id>/provider.py`. Copy the [example manifest](plugins/example-catalog/manifest.json) and give it the same ID as your folder. Declare the server versions, architectures, settings and source names your plugin supports. For a search-only plugin, declare just the `search` entry in `sources`. A separate fetch plugin can use its results when it understands those source names and candidate IDs.

Export a class named `Provider`. It receives a [PluginContext](sdk/listening_plugin/contract.py) and implements `test()` plus the methods declared by its manifest.

| Method | What the room sends | What the provider returns |
| --- | --- | --- |
| `test()` | A context with access to the plugin’s settings | Return normally when configured and ready, or raise a credential-free configuration error. |
| `search(recording)` | Artist, title, album, duration and requested formats | Ranked `Candidate(source, id)` objects. Keep the IDs stable across releases. |
| `fetch(candidate, staging)` | A supported candidate and an empty staging directory | Write one audio file there and return its basename. Listening verifies and stores the audio. |

Read managed settings using `context.settings()`. Declare secret fields in the manifest and set their values in the room. Preserve the plugin ID, setting keys and source names when upgrading a published plugin. The shared SDK handles protocol v1, candidate limits and error responses. Report a completed timeout only after the operation has stopped and released its resources.

The [example catalog](plugins/example-catalog/provider.py) uses the default Python image recipe. [Sockseek](plugins/sockseek/provider.py) keeps its native binary recipe, upstream pin and license under its own `build/` directory. New providers using the default runtime need only their manifest and provider.

## Package and publish

From a clean checkout, validate the metadata and run the contract tests. Build each platform you intend to distribute and record its published digest.

```sh
python3 tools/plugins.py validate
python3 -m unittest discover -s tests
python3 tools/plugins.py build example-catalog --arch amd64 --tag ghcr.io/OWNER/catalog:1.0.2 --push
```

Generate the room’s catalog from the manifests and the immutable images. Supply one `--image` per plugin and architecture in that release. Replace `DIGEST` with the 64-character digest from the registry.

```sh
python3 tools/plugins.py catalog --image example-catalog/amd64=ghcr.io/OWNER/catalog@sha256:DIGEST > catalog.json
openssl dgst -sha256 -sign /private/path/plugin-signing.pem -out catalog.sig catalog.json
```

Attach `catalog.json` and `catalog.sig` to the release and distribute the verified public signing key. Retain the previous digest in the registry so the room can roll back. Update and rollback preserve settings in the room’s credentials directory.

The wrappers are MIT licensed. The Sockseek image redistributes the unmodified Sockseek v3.0.6 binary under AGPL-3.0 and installs the upstream license at `/usr/share/licenses/sockseek/LICENSE`. Include the corresponding source and license links alongside the image digest in release notes. [Pinned source](https://github.com/fiso64/sockseek/archive/refs/tags/v3.0.6.tar.gz) · [Upstream license](plugins/sockseek/build/licenses/Sockseek-AGPL-3.0.txt) · [MIT license](LICENSE)

### Temporary sign-in failures

Raise `LoginUnavailable(retry_after_seconds=1800)` when a source cannot establish a session. The optional delay is 1–86,400 whole seconds. Listening can pause searches and transfers for that source across the room. Use `CompletedTimeout` for an individual peer timeout. Keep account values and upstream messages out of both responses.
