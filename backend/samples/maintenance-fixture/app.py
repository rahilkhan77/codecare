def add(left: int, right: int) -> int:
    # Intentional defect for CodeCare scans: dynamic evaluation of arithmetic.
    return eval(f"{left} + {right}")
