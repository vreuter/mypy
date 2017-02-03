"""Update previous build result by processing changed modules.

Use fine-grained dependencies to update any parts of other modules that
depend on the changed modules.
"""

from typing import Dict, List, Set

from mypy.build import BuildManager, State
from mypy.checker import DeferredNode
from mypy.errors import Errors
from mypy.nodes import MypyFile, FuncItem
from mypy.semanal import FirstPass
from mypy.server.astdiff import compare_symbol_tables
from mypy.server.astmerge import merge_asts
from mypy.server.aststrip import strip_node
from mypy.server.deps import get_dependencies
from mypy.server.subexpr import get_subexpressions
from mypy.server.trigger import make_trigger


def get_all_dependencies(manager: BuildManager) -> Dict[str, Set[str]]:
    """Return fine-grained dependency map for a build."""
    deps = {}  # type: Dict[str, Set[str]]
    for id, node in manager.modules.items():
        module_deps = get_dependencies(prefix=id,
                                       node=node,
                                       type_map=manager.all_types)
        for trigger, targets in module_deps.items():
            deps.setdefault(trigger, set()).update(targets)
    return deps


def update_build(manager: BuildManager,
                 graph: Dict[str, State],
                 deps: Dict[str, Set[str]],
                 changed_modules: List[str]) -> List[str]:
    """Update previous build result by processing changed modules.

    Also propagate changes to other modules as needed.

    TODO: What about blocking errors?

    Args:
        manager: State of the build
        deps: Fine-grained dependcy map for the build (mutated by this function)
        changed_modules: Modules changed since the previous update/build

    Returns:
        A list of errors.
    """
    # TODO: Maybe clean up stale dependencies.
    old_modules = dict(manager.modules)
    new_modules = build_incremental_step(manager, changed_modules)
    update_dependenciess(new_modules, deps)
    triggered = calculate_active_triggers(manager, old_modules, new_modules)
    replace_modules_with_new_variants(manager, old_modules, new_modules)
    propagate_changes_using_dependencies(manager, graph, deps, triggered)
    return manager.errors.messages()


def build_incremental_step(manager: BuildManager,
                           changed_modules: List[str]) -> Dict[str, MypyFile]:
    assert len(changed_modules) == 1
    id = changed_modules[0]
    path = manager.modules[id].path

    # TODO: what if file is missing?
    with open(path) as f:
        source = f.read()

    state = State(id=id,
                  path=path,
                  source=source,
                  manager=manager)  # TODO: more args?
    state.parse_file()
    # TODO: state.fix_suppressed_dependencies()?
    state.semantic_analysis()
    state.semantic_analysis_pass_three()
    state.type_check_first_pass()
    # TODO: state.type_check_second_pass()?
    state.finish_passes()
    # TODO: state.write_cache()?
    # TODO: state.mark_as_rechecked()?

    return {id: state.tree}


def update_dependenciess(new_modules: Dict[str, MypyFile],
                         deps: Dict[str, Set[str]]) -> None:
    # should be kind of easy
    # TODO
    pass


def calculate_active_triggers(manager: BuildManager,
                              old_modules: Dict[str, MypyFile],
                              new_modules: Dict[str, MypyFile]) -> Set[str]:
    names = set()  # type: Set[str]
    for id in new_modules:
        names |= compare_symbol_tables(id, old_modules[id].names, new_modules[id].names)
    return {make_trigger(name) for name in names}


def replace_modules_with_new_variants(
        manager: BuildManager,
        old_modules: Dict[str, MypyFile],
        new_modules: Dict[str, MypyFile]) -> None:
    for id in new_modules:
        if id in old_modules:
            # Remove nodes of old modules from the type map.
            all_types = manager.all_types
            for expr in get_subexpressions(old_modules[id]):
                if expr in all_types:
                    del all_types[expr]
        merge_asts(old_modules[id], old_modules[id].names,
                   new_modules[id], new_modules[id].names)
        manager.modules[id] = old_modules[id]


def propagate_changes_using_dependencies(
        manager: BuildManager,
        graph: Dict[str, State],
        deps: Dict[str, Set[str]],
        triggered: Set[str]) -> None:
    todo = find_targets_recursive(triggered, deps, manager.modules)

    for id, nodes in todo.items():
        file_node = manager.modules[id]
        first = FirstPass(manager.semantic_analyzer)
        for deferred in nodes:
            node = deferred.node
            # Strip semantic analysis information
            strip_node(node)
            # We don't redo the first pass, because it only does local things.
            semantic_analyzer = manager.semantic_analyzer
            with semantic_analyzer.file_context(
                    file_node=file_node,
                    fnam=file_node.path,
                    options=manager.options):
                # Second pass
                manager.semantic_analyzer.refresh_partial(node)
                # Third pass # TODO: Fix for top level
                node.accept(manager.semantic_analyzer_pass3)
        # Type check
        graph[id].type_checker.check_second_pass(list(nodes))  # TODO: check return value


def find_targets_recursive(
        triggers: Set[str],
        deps: Dict[str, Set[str]],
        modules: Dict[str, MypyFile]) -> Dict[str, Set[DeferredNode]]:
    """Find names of all targets to reprocess given certain triggers.

    Returns: Dictionary from module id to a set of stale targets.
    """
    result = {}  # type: Dict[str, Set[DeferredNode]]
    worklist = triggers
    processed = set()  # type: Set[str]

    # Find AST nodes corresponding to each target.
    #
    # TODO: Don't use (only) a set, since items are in an unpredictable order.
    while worklist:
        processed |= worklist
        current = worklist
        worklist = set()
        for target in current:
            if target.startswith('<'):
                worklist |= deps.get(target, set()) - processed
            else:
                module_id = target.split('.', 1)[0]
                if module_id not in result:
                    result[module_id] = set()
                result[module_id].add(lookup_target(modules, target))

    return result


def lookup_target(modules: Dict[str, MypyFile], target: str) -> DeferredNode:
    components = target.split('.')
    node = modules[components[0]]
    for c in components[1:]:
        node = node.names[c].node
    assert isinstance(node, (FuncItem, MypyFile))
    # TODO: Don't use None arguments
    return DeferredNode(node, None, None)
