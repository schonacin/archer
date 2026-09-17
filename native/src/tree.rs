//! Iterative traversal: Ruff's walker is used for one level at a time.
use ruff_python_ast::{
    AnyNodeRef,
    visitor::source_order::{self, SourceOrderVisitor, TraversalSignal},
};

pub fn children(node: AnyNodeRef<'_>) -> Vec<AnyNodeRef<'_>> {
    struct Children<'a> {
        root: bool,
        nodes: Vec<AnyNodeRef<'a>>,
    }
    impl<'a> SourceOrderVisitor<'a> for Children<'a> {
        fn enter_node(&mut self, node: AnyNodeRef<'a>) -> TraversalSignal {
            if self.root {
                self.root = false;
                TraversalSignal::Traverse
            } else {
                self.nodes.push(node);
                TraversalSignal::Skip
            }
        }
    }
    let mut visitor = Children {
        root: true,
        nodes: Vec::new(),
    };
    source_order::walk_node(&mut visitor, node);
    visitor.nodes
}
