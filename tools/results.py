"""
Structured block parser for CalculiX .frd and .dat result files.

Provides parse_frd() which reads the binary/ASCII block structure of a
.frd file to extract max von Mises stress and max displacement, and
parse_dat() which reads reaction forces and strain energy from .dat.
Does NOT use regex — uses the structured block reader approach.
"""

import math
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_node_result_line(line: str):
    """Parse a -1 nodal result record.

    CalculiX .frd format:
      positions 0-2 : ' -1'   (record type, 3 chars)
      positions 3-12: node_id  (I10, right-justified)
      positions 13+ : values   (E12.5 each, 12 chars, no separator)
    """
    node_id = int(line[3:13])
    values = []
    pos = 13
    rline = line.rstrip("\n")
    while pos + 12 <= len(rline):
        chunk = rline[pos:pos + 12]
        try:
            values.append(float(chunk))
        except ValueError:
            values.append(0.0)
        pos += 12
    return node_id, values


def _von_mises(components: list) -> float:
    """Compute von Mises stress from 6 stress tensor components [sxx, syy, szz, sxy, sxz, syz]."""
    if len(components) < 6:
        return 0.0
    sxx, syy, szz, sxy, sxz, syz = components[:6]
    vm = math.sqrt(0.5 * (
        (sxx - syy) ** 2 +
        (syy - szz) ** 2 +
        (szz - sxx) ** 2 +
        6.0 * (sxy ** 2 + sxz ** 2 + syz ** 2)
    ))
    return vm


def _detect_block(line: str) -> "str | None":
    """
    Detect a result block header from a ` -4` record.
    Returns "DISP", "STRESS", or None.
    """
    upper = line.upper()
    if "DISP" in upper:
        return "DISP"
    if "STRESS" in upper:
        return "STRESS"
    return None


def parse_frd(frd_path: Path) -> dict:
    """
    Parse a CalculiX .frd file using the structured block reader.

    Real .frd format (CalculiX 2.x):
      ` -4  DISP/STRESS ...`  — result block header (1 space + -4)
      ` -5  COMPNAME ...`     — component sub-record
      ` -1  node_id  v1 v2 …` — nodal value (1 space + -1)
      ` -3`                   — end of block

    Returns
    -------
    dict with keys:
        max_von_mises_pa   : float
        max_displacement_m : float
        node_count         : int
    """
    frd_path = Path(frd_path)
    logger.info("Parsing .frd: %s", frd_path)

    node_count = 0
    max_displacement_m = 0.0
    node_vm: dict[int, float] = {}

    current_block = None  # "DISP" | "STRESS" | None

    with frd_path.open(encoding="latin-1") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if len(line) < 3:
                continue

            # Result block header: " -4  DISP..." or " -4  STRESS..."
            if line.startswith(" -4"):
                current_block = _detect_block(line)
                logger.debug("Entering block: %s", current_block)
                continue

            # End of block
            if line.startswith(" -3"):
                logger.debug("Exiting block: %s", current_block)
                current_block = None
                continue

            # Skip component sub-records
            if line.startswith(" -5"):
                continue

            # Nodal value record (only meaningful inside a result block)
            if line.startswith(" -1") and current_block is not None:
                try:
                    node_id, values = _parse_node_result_line(line)
                except (ValueError, IndexError):
                    continue

                if current_block == "DISP" and len(values) >= 3:
                    dx, dy, dz = values[0], values[1], values[2]
                    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
                    node_count += 1  # count nodes from DISP block
                    if mag > max_displacement_m:
                        max_displacement_m = mag

                elif current_block == "STRESS" and len(values) >= 6:
                    vm = _von_mises(values)
                    if node_id not in node_vm or vm > node_vm[node_id]:
                        node_vm[node_id] = vm

    max_von_mises_pa = max(node_vm.values()) if node_vm else 0.0

    logger.info(
        "FRD parsed: nodes=%d, max_vm=%.2f MPa, max_disp=%.4f mm",
        node_count, max_von_mises_pa / 1e6, max_displacement_m * 1e3,
    )
    return {
        "max_von_mises_pa":   max_von_mises_pa,
        "max_displacement_m": max_displacement_m,
        "node_count":         node_count,
    }


def parse_frd_nodal(frd_path: Path) -> tuple[dict, dict]:
    """
    Parse a CalculiX .frd file and return per-node scalar fields.

    Returns
    -------
    (disp_mag, von_mises) where each is a dict {node_id: float}
    """
    frd_path = Path(frd_path)
    disp_mag: dict[int, float] = {}
    node_vm:  dict[int, float] = {}

    current_block = None

    with frd_path.open(encoding="latin-1") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if len(line) < 3:
                continue

            if line.startswith(" -4"):
                current_block = _detect_block(line)
                continue

            if line.startswith(" -3"):
                current_block = None
                continue

            if line.startswith(" -5"):
                continue

            if line.startswith(" -1") and current_block is not None:
                try:
                    node_id, values = _parse_node_result_line(line)
                except (ValueError, IndexError):
                    continue

                if current_block == "DISP" and len(values) >= 3:
                    dx, dy, dz = values[0], values[1], values[2]
                    disp_mag[node_id] = math.sqrt(dx*dx + dy*dy + dz*dz)

                elif current_block == "STRESS" and len(values) >= 6:
                    vm = _von_mises(values)
                    if node_id not in node_vm or vm > node_vm[node_id]:
                        node_vm[node_id] = vm

    return disp_mag, node_vm


def parse_dat(dat_path: Path) -> dict:
    """
    Parse a CalculiX .dat file for reaction forces and strain energy.

    Returns
    -------
    dict with keys:
        reaction_forces_n : dict {fx, fy, fz} (sum of all reaction forces)
        strain_energy_j   : float
    """
    dat_path = Path(dat_path)
    logger.info("Parsing .dat: %s", dat_path)

    fx_total, fy_total, fz_total = 0.0, 0.0, 0.0
    strain_energy = 0.0

    with dat_path.open(encoding="latin-1") as fh:
        lines = fh.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].lower()

        # Reaction forces table
        if "forces" in line and "reaction" in line:
            # Skip header lines until we hit numeric data
            j = i + 1
            while j < len(lines):
                data_line = lines[j].strip()
                if not data_line:
                    j += 1
                    continue
                parts = data_line.split()
                # expect: node_id  fx  fy  fz
                if len(parts) >= 4:
                    try:
                        fx_total += float(parts[1])
                        fy_total += float(parts[2])
                        fz_total += float(parts[3])
                        j += 1
                    except ValueError:
                        break
                else:
                    break
            i = j
            continue

        # Strain energy
        if "strain energy" in line:
            parts = lines[i].split()
            for part in parts:
                try:
                    val = float(part)
                    strain_energy = val
                    break
                except ValueError:
                    continue

        i += 1

    logger.info(
        "DAT parsed: reactions=(%.2f, %.2f, %.2f) N, strain_energy=%.4e J",
        fx_total, fy_total, fz_total, strain_energy,
    )
    return {
        "reaction_forces_n": {"fx": fx_total, "fy": fy_total, "fz": fz_total},
        "strain_energy_j":   strain_energy,
    }
