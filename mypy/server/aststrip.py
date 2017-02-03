"""Strip AST from from semantic and type information."""

from mypy.nodes import Node, FuncDef, NameExpr, MemberExpr, RefExpr
from mypy.traverser import TraverserVisitor


def strip_node(node: Node) -> None:
    node.accept(NodeStripVisitor())


class NodeStripVisitor(TraverserVisitor):
    def visit_func_def(self, node: FuncDef) -> None:
        node.expanded = []
        node.type = node.unanalyzed_type
        super().visit_func_def(node)

    def visit_name_expr(self, node: NameExpr) -> None:
        self.visit_ref_expr(node)

    def visit_member_expr(self, node: MemberExpr) -> None:
        self.visit_ref_expr(node)

    def visit_ref_expr(self, node: RefExpr) -> None:
        node.kind = None
        node.node = None
        node.fullname = None

    # TODO: handle more node types
