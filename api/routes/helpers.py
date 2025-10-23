import traceback
from typing import Any

from fastapi import HTTPException
from pipelex import log
from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
from pipelex.core.concepts.concept import Concept
from pipelex.core.interpreter import PipelexInterpreter
from pipelex.core.pipe_errors import PipeDefinitionError
from pipelex.core.pipes.pipe_abstract import PipeAbstract
from pipelex.exceptions import DryRunError
from pipelex.hub import get_class_registry, get_library_manager
from pipelex.pipe_run.dry_run import dry_run_pipes


def get_concept_structure(concept: Concept) -> dict[str, Any]:
    """Extract structure information for a concept.

    Args:
        concept: Concept object to extract structure from

    Returns:
        Dictionary containing:
        {
            "concept_code": str,
            "structure_class_name": str,
            "class_structure": {schema from Pydantic model_json_schema}
        }
    """
    class_registry = get_class_registry()

    try:
        structure_class = class_registry.get_required_class(concept.structure_class_name)
        class_structure = structure_class.model_json_schema()

        return {
            "concept_code": concept.code,
            "structure_class_name": concept.structure_class_name,
            "class_structure": class_structure,
        }
    except Exception as e:
        return {
            "concept_code": concept.code,
            "structure_class_name": concept.structure_class_name,
            "error": str(e),
        }


def extract_pipe_structures(pipes: list[PipeAbstract]) -> dict[str, dict[str, Any]]:
    """Extract structure information for a list of pipes.

    For each pipe, extracts input and output concept structures.

    Args:
        pipes: List of PipeAbstract objects to extract structures from

    Returns:
        Dictionary mapping pipe_code to structure information:
        {
            "pipe_code": {
                "inputs": {
                    "input_name": {concept_structure},
                    ...
                },
                "output": {concept_structure}
            },
            ...
        }
    """
    pipe_structures: dict[str, dict[str, Any]] = {}

    for pipe in pipes:
        inputs_specs: dict[str, Any] = {}

        # Process inputs - extract concept structures
        for input_name, input_spec in pipe.inputs.root.items():
            concept = input_spec.concept
            inputs_specs[input_name] = get_concept_structure(concept)

        # Process output - extract concept structure
        output_spec = get_concept_structure(pipe.output)

        # Store structure info for this pipe
        pipe_structures[pipe.code] = {
            "inputs": inputs_specs,
            "output": output_spec,
        }

    return pipe_structures


async def validate_and_load_pipes(plx_content: str) -> tuple[PipelexBundleBlueprint, list[PipeAbstract], dict[str, dict[str, Any]]]:
    """Validate and load pipes from PLX content.

    This function:
    1. Parses PLX content into a bundle blueprint
    2. Loads pipes from the blueprint
    3. Runs static validation and dry runs
    4. Extracts pipe structures

    Args:
        plx_content: The PLX content to validate and load

    Returns:
        Tuple of (blueprint, pipes, pipe_structures)

    Raises:
        HTTPException: With 400 status code for validation errors, 500 for unexpected errors
    """
    library_manager = get_library_manager()
    blueprint: PipelexBundleBlueprint | None = None

    try:
        # Parse PLX content into a bundle blueprint
        converter = PipelexInterpreter(file_content=plx_content)
        blueprint = converter.make_pipelex_bundle_blueprint()

        # Load pipes from the blueprint
        pipes = library_manager.load_from_blueprint(blueprint=blueprint)

        # Extract pipe structures using utility function
        pipe_structures = extract_pipe_structures(pipes)

        # Validate all pipes
        for pipe in pipes:
            pipe.validate_with_libraries()
            await dry_run_pipes(pipes=[pipe], raise_on_failure=True)

        return blueprint, pipes, pipe_structures

    except PipeDefinitionError as exc:
        log.error("PipeDefinitionError details:")
        traceback.print_exc()

        # Clean up if blueprint was created
        try:
            if blueprint is not None:
                library_manager.remove_from_blueprint(blueprint=blueprint)
        except Exception as cleanup_error:
            log.error(f"Error during cleanup: {cleanup_error}")

        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "PipeDefinitionError",
                "error": exc.__dict__,
            },
        ) from exc

    except DryRunError as exc:
        log.error("DryRunError details:")
        traceback.print_exc()

        # Clean up if blueprint was created
        try:
            if blueprint is not None:
                library_manager.remove_from_blueprint(blueprint=blueprint)
        except Exception as cleanup_error:
            log.error(f"Error during cleanup: {cleanup_error}")

        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "DryRunError",
                "error": exc.__dict__,
            },
        ) from exc

    except Exception as exc:
        log.error("Unexpected validation error details:")
        traceback.print_exc()

        # Clean up if blueprint was created
        try:
            if blueprint is not None:
                library_manager.remove_from_blueprint(blueprint=blueprint)
        except Exception as cleanup_error:
            log.error(f"Error during cleanup: {cleanup_error}")

        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        ) from exc
