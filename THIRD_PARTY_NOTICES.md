# Third-party notices

## OpenVSP / VSPAERO

The portable package includes the unmodified official OpenVSP 3.51.3 Windows
x64 distribution built for Python 3.13.

- Project and source code: https://github.com/OpenVSP/OpenVSP
- Official download: https://openvsp.org/download.php
- Bundled license: `engines/OpenVSP/LICENSE`
- Upstream changelog: `engines/OpenVSP/CHANGELOG.md`
- Download archive SHA-256:
  `BA885EBC592FEB57BCF7F8895662CE68A130797550296CFAB4B3E6AC93F59CEB`

OpenVSP is distributed under the NASA Open Source Agreement (NOSA) 1.3. The
bundled binaries have not been modified. The complete corresponding source is
available from the project link above.

## MachLine

MachLine is developed by USU Aero Lab and distributed under the MIT License.

- Project: https://github.com/usuaero/MachLine
- License copy: `engines/MachLine/LICENSE`

Local changes include diagnostic protection for control-point placement, output-format compatibility fixes, and compiler-compatibility adjustments. These changes are documented in the included source tree.

## Python

The portable Python runtime retains its upstream license text at:

```text
runtime/python/LICENSE.txt
```

## GCC runtime libraries

The MachLine runtime directory contains GCC/MinGW runtime libraries required to execute the Windows binary. Their upstream license terms and applicable runtime exceptions remain in effect.

