#!/usr/bin/env python3
"""Test pisoFoam cavity (RAS) case with multi-round error correction.

Purpose: Reproduce the fvSolution missing pFinal issue with PISO solver
(pisoFoam + kEpsilon RAS turbulence model).

The reference fvSolution for pisoFoam cavity includes pFinal:
    pFinal { $p; tolerance 1e-06; relTol 0; }

Without pFinal, OpenFOAM v10 will fail with:
    keyword pFinal is undefined in dictionary "system/fvSolution/solvers"

Usage:
    export FOAMAGENT_MODEL_PROVIDER=deepseek
    export FOAMAGENT_MODEL_VERSION=deepseek-v4-pro
    export FOAMAGENT_REASONING_EFFORT=max
    export DEEPSEEK_API_KEY=your-key

    python tests/test_pisoFoam_cavity_multirun.py
"""

import os
import re
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from services.plan import (
    parse_requirement_to_case_info,
    resolve_case_dir,
    generate_simulation_plan
)
from services.input_writer import initial_write, rewrite_files
from services.mesh import prepare_standard_mesh
from services.run_local import run_allrun_and_collect_errors
from services.review import review_error_logs, generate_rewrite_plan
from utils import scan_case_directory, read_case_foamfiles
from services.visualization import (
    ensure_foam_file,
    generate_deterministic_pyvista_script,
    run_pyvista_script
)
from config import Config


def check_pfinal(case_dir: str) -> list[str]:
    """Check if fvSolution has pFinal entry."""
    issues = []
    path = os.path.join(case_dir, "system", "fvSolution")
    if not os.path.exists(path):
        issues.append("fvSolution not found")
        return issues
    with open(path, 'r') as f:
        content = f.read()
    if not re.search(r'\bpFinal\b', content):
        issues.append("Missing pFinal sub-dictionary")
    if "PISO" not in content:
        issues.append("Missing PISO sub-dictionary")
    return issues


def main():
    print("=" * 60)
    print("  pisoFoam Cavity (RAS) — pFinal Issue Reproduction")
    print("=" * 60)

    config = Config()

    user_requirement = """
Simulate a 2D lid-driven cavity flow using pisoFoam solver with kEpsilon RAS turbulence model.

Setup:
- Solver: pisoFoam
- Turbulence: RAS with kEpsilon model
- Kinematic viscosity nu = 1e-5 m^2/s
- Square cavity of 0.1m x 0.1m, thin in z (0.01m) for 2D
- Mesh: 20x20 in x-y, 1 cell in z
- Top wall (movingWall) moves at (1 0 0) m/s
- Other walls (fixedWalls) are no-slip
- Front and back faces are empty (2D)
- Simulation: 0 to 10 seconds, dt = 0.005, write every 100 steps

Initial turbulence fields:
- k = 0.00375 m^2/s^2
- epsilon = 0.00754 m^2/s^3
- omega = 22.4 1/s
- nut = 0 (with nutkWallFunction)
- nuTilda = 0
"""

    results = {}

    try:
        # Step 1: Create case
        print("\nStep 1: Creating case")
        print("-" * 40)

        case_stats_path = os.path.join(config.database_path, "raw", "openfoam_case_stats.json")
        with open(case_stats_path, 'r') as f:
            case_stats = json.load(f)

        case_info = parse_requirement_to_case_info(user_requirement, case_stats)

        case_dir = os.path.abspath(resolve_case_dir(
            case_name="pisoFoam_cavity",
            case_dir="./output_pisoFoam_cavity_test",
            run_times=config.run_times
        ))
        os.makedirs(case_dir, exist_ok=True)
        print(f"  Case dir: {case_dir}")
        results['case_creation'] = True

        # Step 2: Plan
        print("\nStep 2: Planning")
        print("-" * 40)

        plan_data = generate_simulation_plan(
            user_requirement=user_requirement,
            case_stats=case_stats,
            case_dir=case_dir,
            searchdocs=config.searchdocs,
        )
        print(f"  Solver: {plan_data['case_solver']}")
        print(f"  Subtasks: {len(plan_data['subtasks'])}")
        for i, st in enumerate(plan_data['subtasks']):
            print(f"    {i+1}. {st['file_name']} in {st['folder_name']}")
        results['planning'] = True

        # Step 3: Generate files
        print("\nStep 3: Generating files")
        print("-" * 40)

        tutorial_reference = plan_data["tutorial_reference"]
        allrun_reference = plan_data["allrun_reference"]
        case_info_str = (
            f"case name: {plan_data['case_name']}\n"
            f"case domain: {plan_data['case_domain']}\n"
            f"case category: {plan_data['case_category']}\n"
            f"case solver: {plan_data['case_solver']}"
        )
        write_result = initial_write(
            case_dir=case_dir,
            subtasks=plan_data["subtasks"],
            user_requirement=user_requirement,
            tutorial_reference=tutorial_reference,
            case_solver=plan_data["case_solver"],
            case_info=case_info_str,
            allrun_reference=allrun_reference,
            database_path=str(config.database_path),
            searchdocs=config.searchdocs
        )
        dir_structure = write_result.get("dir_structure", {})
        file_count = sum(len(files) for files in dir_structure.values())
        print(f"  Generated {file_count} files in {len(dir_structure)} directories")
        results['file_generation'] = True

        # Step 3b: Pre-run check
        print("\nStep 3b: Pre-run fvSolution check")
        print("-" * 40)

        pre_issues = check_pfinal(case_dir)
        if pre_issues:
            print("  MISSING ENTRIES:")
            for issue in pre_issues:
                print(f"    - {issue}")
            results['pre_run_check'] = False
        else:
            print("  fvSolution complete (pFinal + PISO present)")
            results['pre_run_check'] = True

        # Print fvSolution
        fvsol_path = os.path.join(case_dir, "system", "fvSolution")
        if os.path.exists(fvsol_path):
            print(f"\n  --- Generated fvSolution ---")
            with open(fvsol_path, 'r') as f:
                print(f.read())
            print(f"  --- end ---")

        # Step 4: Mesh
        print("\nStep 4: Preparing mesh")
        print("-" * 40)
        prepare_standard_mesh(user_requirement, case_dir)
        results['mesh_preparation'] = True

        # Step 5: Run + Review loop
        max_loop = config.max_loop
        loop_count = 0
        history_text = None
        foamfiles = None
        final_success = False
        pfinal_error = False

        while loop_count < max_loop:
            loop_num = loop_count + 1
            print(f"\n{'='*60}")
            print(f"  Iteration {loop_num}/{max_loop}")
            print(f"{'='*60}")

            # Run
            print(f"\n  Step 5.{loop_num}a: Running simulation")
            errors = run_allrun_and_collect_errors(case_dir=case_dir, timeout=600, max_retries=3)
            error_logs = errors if isinstance(errors, list) else [str(err) for err in errors]

            if not error_logs:
                print("  No errors!")
                final_success = True
                break

            print(f"  Errors: {len(error_logs)}")
            for e in error_logs[:5]:
                print(f"    - {e}")
                if "pFinal" in str(e):
                    pfinal_error = True
                    print(f"    >>> pFinal ISSUE DETECTED <<<")

            # Review
            print(f"\n  Step 5.{loop_num}b: Reviewing")
            dir_structure = scan_case_directory(case_dir)
            foamfiles = read_case_foamfiles(case_dir, dir_structure)
            review_content, history_text = review_error_logs(
                tutorial_reference=tutorial_reference,
                foamfiles=foamfiles,
                error_logs=error_logs,
                user_requirement=user_requirement,
                history_text=history_text
            )

            # Rewrite
            print(f"\n  Step 5.{loop_num}c: Applying fixes")
            rewrite_plan = generate_rewrite_plan(
                foamfiles=foamfiles,
                error_logs=error_logs,
                review_analysis=review_content,
                user_requirement=user_requirement,
            )
            rewrite_result = rewrite_files(
                case_dir=case_dir,
                error_logs=error_logs,
                review_analysis=review_content,
                rewrite_plan=rewrite_plan,
                user_requirement=user_requirement,
                foamfiles=foamfiles,
                dir_structure=dir_structure,
            )
            dir_structure = rewrite_result["dir_structure"]
            foamfiles = rewrite_result["foamfiles"]

            # Post-fix check
            post_issues = check_pfinal(case_dir)
            print(f"  Post-fix pFinal: {'OK' if not post_issues else post_issues}")

            loop_count += 1

        # Final status
        print(f"\n{'='*60}")
        print(f"  Result: {'Success' if final_success else 'Failed'} after {loop_count} loop(s)")
        print(f"{'='*60}")
        results['simulation_run'] = final_success
        results['review'] = True

        # Step 6: Visualization
        if final_success:
            print("\nStep 6: Visualization")
            print("-" * 40)
            foam_file = ensure_foam_file(case_dir)
            foam_path = os.path.join(case_dir, foam_file)
            script = generate_deterministic_pyvista_script(
                foam_file=foam_path, output_png="velocity.png", field_preference="U"
            )
            ok, img, errs = run_pyvista_script(case_dir, script, expected_png="velocity.png")
            print(f"  {'Generated: ' + img if ok else 'Issues: ' + str(errs)}")
            results['visualization'] = ok

        # Summary
        print(f"\n{'='*60}")
        print("  SUMMARY")
        print(f"{'='*60}")
        for name, result in results.items():
            print(f"  {name:25} {'PASS' if result else 'FAIL'}")

        passed = sum(1 for v in results.values() if v)
        print(f"\n  Overall: {passed}/{len(results)}")

        print(f"\n  pFinal ISSUE REPORT:")
        print(f"    Pre-run missing:   {not results.get('pre_run_check', True)}")
        print(f"    Runtime error:     {pfinal_error}")
        print(f"    Loops consumed:    {loop_count}")

        return 0 if passed == len(results) else 1

    except Exception as e:
        print(f"\nException: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
