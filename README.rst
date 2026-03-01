tec-suite v0.7.8
----------------

Software to reconstruct slant total electron content (TEC) value in
the ionosphere using data derived from global navigation satellite
systems such as GPS, GLONASS, etc.

Quick Start
===========

**Single RINEX file:**

.. code-block:: bash

    tecs              # edit tecs.cfg first
    # or
    python tecs.py

**Batch processing zipped RINEX archives:**

If you have a directory with day folders (``01``, ``02``, etc.) containing
``*.zip`` files of RINEX data, use the batch runner:

.. code-block:: bash

    python process_rinex.py -r /path/to/rinex/root -c tecs.cfg -t tecs.py -j 4 -v -k

Options:

* ``-r``, ``--root`` : root directory containing day folders
* ``-c``, ``--cfg`` : path to configuration file
* ``-t``, ``--tecs`` : path to tecs.py script
* ``-j``, ``--jobs`` : number of parallel jobs (default 1)
* ``-o``, ``--out`` : override output directory (optional)
* ``-v``, ``--verbose`` : verbose output
* ``-k``, ``--cleanup`` : delete extracted folders after processing

The batch runner:

1. Scans the root for day folders (names matching digits: ``1``, ``01``, ``001``, etc.).
2. For each day folder, finds all ``*.zip`` archives.
3. Unzips each archive into a sibling directory.
4. Runs ``tecs`` on it (with temporary config to avoid race conditions).
5. Optionally cleans up the extracted directory.
6. Logs progress to ``out/process_rinex.log``.

All archives in a day are processed sequentially; you can specify ``-j N`` to run
N independent days in parallel.

Docker
======

Build the image:

.. code-block:: bash

    docker build -t tec-suite .

Run via docker-compose:

.. code-block:: bash

    # print help
    docker-compose up

    # process RINEX with override output directory
    docker-compose run --rm \
      -v /host/rinex:/data/rinex \
      -v /host/out:/app/out \
      tecsuite -r /data/rinex -c /app/tecs.cfg -lo /app/out -j 4 -k

Or run directly:

.. code-block:: bash

    docker run --rm -v /host/rinex:/data/rinex -v /host/out:/app/out \
      tec-suite -r /data/rinex -c /app/tecs.cfg -t /app/tecs.py -o /app/out -j 4 -k

Features
========

For the moment **tec-suite** supports:

* Navigation systems:

  * GPS
  * GLONASS
  * Galileo
  * Compass/BeiDou
  * GEO
  * IRNSS

* RINEX versions:

  * v2 (2.0 - 2.11)
  * v3 (3.0 - 3.03)

* File types:

  * RINEX observation files
  * Hatanaka-compressed RINEX observation files
  * RINEX navigation files
  * compressed (.Z or .gz) files

Documentation
=============

You can find the documentation at readthedocs.org_.

.. _readthedocs.org: http://tec-suite.readthedocs.io

Installation
============

Just download and extract **tec-suite** archive wherever you want.

Downloads
~~~~~~~~~

* `Windows <https://github.com/gnss-lab/tec-suite/releases/download/v0.7.8/tec-suite-v0.7.8-win32.zip>`_
* Linux: x86_32_ and x86_64_
* `macOS <https://github.com/gnss-lab/tec-suite/releases/download/v0.7.8/tec-suite-v0.7.8-macos.tgz>`_

.. _x86_32: https://github.com/gnss-lab/tec-suite/releases/download/v0.7.8/tec-suite-v0.7.8-linux32.tgz
.. _x86_64: https://github.com/gnss-lab/tec-suite/releases/download/v0.7.8/tec-suite-v0.7.8-linux64.tgz

Requirements
~~~~~~~~~~~~

``crx2rnx``
    To decompress Hatanaka-compressed RINEX files, **tec-suite** uses
    `crx2rnx <http://terras.gsi.go.jp/ja/crx2rnx.html>`_.

``gunzip``
    To unarchive ``.z``, ``.Z`` or ``.gz``, files **tec-suite**
    uses ``gunzip``. If your system is **Linux** or **macOS** you
    probably have it installed. You can find the **Windows** version
    at `GnuWin <http://gnuwin32.sourceforge.net/packages/gzip.htm>`_
    site.

Usage
=====

**Single file mode:**

1. Edit ``tecs.cfg`` with paths to your RINEX data.
2. Run ``tecs`` or ``python tecs.py``.

**Batch mode (zipped archives):**

Processes multiple RINEX archives in parallel. See "Quick Start" section above.

For detailed usage, feature options and output format specification, see `the
full documentation`__.

__ readthedocs.org_

Bugs
====

Report any bugs via project's
`issue tracker <https://github.com/gnss-lab/tec-suite/issues>`_.
Feel free to fork and play with the code. I will appreciate fixes
and suggestions.

License
=======

Copyright (c) 2017 Ilya Zhivetiev <i.zhivetiev@gnss-lab.org>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
