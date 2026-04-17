# hb_downloader

Simple script to download purchases from humble bundle account.

By default, script download multiple files at once, starting with the
biggest files. Script will only download files specified by platform
parameter. Platform parameter is based on platform selection in [HB
Library](https://www.humblebundle.com/home/library).

Script only download files once even if it was purchased in multiple
bundles. It will be saved in the oldest bundle/purchase folder.

To not download files multiple times, hash of file is computed and
compared with one provided by HB, so if HB will update files it will
overwrite existing one.

By default, file are saved in following folder structure:

platform/date of purchase bundle name/item name

This structure helps with importing e-books into calibre, so each e-book
will be in separate folder with multiple available formats.

## Requirements

Python in version 3.x or higher, accessible in command line

Script require few additional non-standard packages. Install them with

``` bash
pip install python-slugify ruamel.yaml requests loguru --user
```

For Windows systems script require removing 260 path name limit,
otherwise download of some files will fail or script will try to
redownload files with long file names.

## Usage

Make config.yaml file based on example_config.yaml and provide
`_simpleauth_sess` cookie from browser between apostrophes.

Providing switches for download_limit and purchase_limit ignore default
values from config.yaml file.

``` bash
usage: hb_downloader.py [-h] [-l X] [-n Y] [-s] [-w] [-m] [-r] [-v] [-d]
                        [--log-output {stdout,file,both}] [--log-file FILENAME]
                        [--log-dir DIR]
                        [platform ...]

Download files from Humble Bundle, based on selected platform

positional arguments:
  platform              platform to download, valid platforms are: ['android', 'audio',
                        'ebook', 'linux', 'mac', 'windows', 'video', 'other', 'nogames', 'all']

options:
  -h, --help                        show this help message and exit
  -l X, --download_limit X          Download X files in parallel
  -n Y, --purchase_limit Y          Download only from Y newest purchases, 0 for all (default)
  -s, --smallest_first              Download smallest files first
  -w, --keep_wrong_size             Download files with wrong size and keep them
  -m, --keep_wrong_md5sum           Keep files with wrong md5sum
  -r, --skip_recheck                Skip MD5 verification for files that already exist
                                    with the correct size
  -v, --verbose                     Report each item as it is processed
  -d, --dry-run                     Report size/MD5 mismatches and missing files without
                                    deleting or downloading anything
  --log-output {stdout,file,both}   Where to write log output (default: stdout)
  --log-file FILENAME               Log file name (default: hb_downloader_output_<datetime>.log)
  --log-dir DIR                     Directory for log files (default: logs/ under the
                                    script directory)
```

You can specify multiple platforms to download, for example to download
all audio albums, and all e-book files:

``` bash
python hb_downloader.py audio ebook
```

To download all files from HB account use:

``` bash
python hb_downloader.py all
```

To download all files, but not games:

    python hb_downloader.py nogames

By default, script download multiple files at once, starting with the
biggest files. You can limit maximum parallel downloads by specifying
download-limit, to download 6 audio files at once use:

    python hb_downloader.py -l 6 audio

To download only files from the latest purchase use purchase-limit
option, for example to download only e-books from last 5 bundles use:

    python hb_downloader.py -n 5 ebook

Above example will skip files if they were in previous bundles
purchased, so it recommended use is to update downloaded collection by
newest purchased bundles not downloaded before. If purchase-limit option
is omitted all previously downloaded files will have their hash
recomputed which will slow down overall process of downloading.

### Keep files flags

Added flags to keep files with wrong reported sizes and hashes. Humble no longer
provide md5sum on their websites, when downloading, so i stopped nagging them
that sums and/or sizes are incorrect. So to keep downloading all books I added
such flag.
There are some files with wrong size, but correct hash, but most of them will
have both wrong. So you probably want to use both flags.

### Dry run

Use `-d` / `--dry-run` to audit your library without making any changes:

``` bash
python hb_downloader.py -d all
```

Dry run reports missing files, size mismatches, and MD5 failures but never
deletes, downloads, or updates the state YAML files.

### Logging

By default logs are written to stdout only. Use `--log-output` to redirect:

``` bash
# Write to both terminal and a dated log file under logs/
python hb_downloader.py --log-output both all

# Write to a file only, in a custom directory
python hb_downloader.py --log-output file --log-dir /var/log/hb ebook
```

All three logging options can also be set in `config.yaml`:

``` yaml
log_output: both
log_dir: /path/to/logs
log_file: my_run.log   # optional; auto-named by date/time when omitted
```

### State files

The script maintains three YAML tracking files inside `download_folder`:

| File | Purpose |
|------|---------|
| `hb_downloader_downloaded.yaml` | Products verified or downloaded in this session |
| `hb_downloader_not-downloaded.yaml` | Products not yet downloaded |
| `hb_downloader_orphaned.yaml` | Products removed from the HB catalogue |

Keeping the state files next to the downloaded content means each
`download_folder` is self-contained — you can point multiple runs at
different folders without them interfering with each other.

### podman/docker

On 2022-08-08 created podman/docker image for easy use.

To download image use:

``` bash
podman pull ghcr.io/tokariew/hb_downloader:latest
```

Basic run looks like:

``` bash
podman run -it --rm -v .:/srv:z hb_downloader:latest all
```

Above command at first run will create `config.yaml` file and fail, edit
the file by providing `session_cookie` and run it again. Don't edit
`download_folder` unless you properly change container volume. Files
will be downloaded into current folder. You can change it by changing
volume location for example:

``` bash
podman run -it --rm -v ~/Downloads/hb:/srv:z hb_downloader:latest all
```
