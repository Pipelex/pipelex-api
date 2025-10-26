from typing import Any

from pipelex.core.concepts.concept import Concept
from pipelex.core.pipes.pipe_abstract import PipeAbstract
from pipelex.hub import get_class_registry


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
