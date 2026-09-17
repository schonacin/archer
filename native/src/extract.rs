use crate::{
    scope::{Binding, NameMap, Names, Scope, absolute, full_name, lookup},
    tree::children,
};
use ruff_python_ast::{AnyNodeRef as N, Expr, ExprContext};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};
use std::collections::BTreeMap;

const MAX_DEPTH: usize = 512;
const MAX_NODES: usize = 1_000_000;

#[derive(Clone)]
struct Visit<'a> {
    node: N<'a>,
    scope: usize,
    owner: String,
    kind: &'static str,
    depth: usize,
    store_after: Option<u32>,
}
struct Extraction<'a> {
    source: &'a str,
    module: &'a str,
    file: &'a str,
    scopes: Vec<Scope>,
    visits: Vec<Visit<'a>>,
    builtins: Names,
    nodes: Vec<Value>,
    pending: Vec<Value>,
    exports: NameMap,
    attributes: NameMap,
    bindings: NameMap,
    rebindings: Names,
    receivers: BTreeMap<String, String>,
    counts: BTreeMap<String, usize>,
    child_scopes: BTreeMap<u32, usize>,
    lines: Vec<usize>,
}
impl<'a> Extraction<'a> {
    fn bind(&mut self, scope: usize, local: &str, name: String, position: u32) {
        self.scopes[scope]
            .bindings
            .entry(local.to_owned())
            .or_default()
            .push(Binding { name, position });
    }
    fn local(&mut self, scope: usize, name: &str, position: u32) {
        let target = self.scopes[scope]
            .redirects
            .get(name)
            .copied()
            .unwrap_or(scope);
        let qualified = format!("{}.{name}", self.scopes[target].prefix);
        self.bind(target, name, qualified, position);
    }
    fn new_scope(&mut self, parent: usize, name: &str, kind: &'static str) -> usize {
        let id = self.scopes.len();
        self.scopes.push(Scope::new(
            Some(parent),
            format!("{}.{name}", self.scopes[parent].prefix),
            kind,
        ));
        id
    }
    fn layout(&mut self, body: &'a [ruff_python_ast::Stmt]) -> Result<(), String> {
        let mut stack: Vec<_> = body
            .iter()
            .rev()
            .map(|n| Visit {
                node: N::from(n),
                scope: 0,
                owner: self.module.to_owned(),
                kind: "module",
                depth: 1,
                store_after: None,
            })
            .collect();
        while let Some(mut v) = stack.pop() {
            if v.depth > MAX_DEPTH || self.visits.len() >= MAX_NODES {
                return Err(format!(
                    "complexity: AST exceeds depth {MAX_DEPTH} or node budget {MAX_NODES}"
                ));
            }
            let outer = v.scope;
            let mut inner = outer;
            let mut kids = children(v.node);
            match v.node {
                N::StmtFunctionDef(n) => {
                    self.local(outer, n.name.as_str(), n.range.start().to_u32());
                    inner = self.new_scope(outer, n.name.as_str(), "function");
                    self.child_scopes.insert(n.range.start().to_u32(), inner);
                    let name = format!("{}.{}", v.owner, n.name);
                    let count = self.counts.entry(name.clone()).or_default();
                    *count += 1;
                    let id = if *count == 1 {
                        name.clone()
                    } else {
                        format!("{name}#{count}")
                    };
                    let kind = if v.kind == "class" {
                        "method"
                    } else {
                        "function"
                    };
                    self.nodes.push(json!({"id":id,"name":name,"kind":kind,"parent":v.owner,
                        "range":self.declaration_location(n.range, n.name.range(), if n.is_async {"async"} else {"def"}), "code_start": n.decorator_list.first().map_or(n.range.start(), |d| d.range.start()).to_u32(), "code_end":n.range.end().to_u32()}));
                    v.owner = id;
                    v.kind = kind;
                }
                N::StmtClassDef(n) => {
                    self.local(outer, n.name.as_str(), n.range.start().to_u32());
                    inner = self.new_scope(outer, n.name.as_str(), "class");
                    self.child_scopes.insert(n.range.start().to_u32(), inner);
                    let name = format!("{}.{}", v.owner, n.name);
                    let count = self.counts.entry(name.clone()).or_default();
                    *count += 1;
                    let id = if *count == 1 {
                        name.clone()
                    } else {
                        format!("{name}#{count}")
                    };
                    self.nodes.push(json!({"id":id,"name":name,"kind":"class","parent":v.owner,
                        "range":self.declaration_location(n.range, n.name.range(), "class"), "code_start":n.decorator_list.first().map_or(n.range.start(), |d| d.range.start()).to_u32(),"code_end":n.range.end().to_u32()}));
                    v.owner = id;
                    v.kind = "class";
                }
                N::ExprLambda(_) => {
                    inner = self.new_scope(outer, "<locals>", "lambda");
                }
                N::ExprListComp(_)
                | N::ExprSetComp(_)
                | N::ExprDictComp(_)
                | N::ExprGenerator(_) => {
                    inner = self.new_scope(outer, "<comprehension>", "comprehension");
                }
                N::Parameter(n) => self.local(outer, n.name.as_str(), 0),
                N::ExprName(n) if n.ctx == ExprContext::Store => {
                    let position = if self.scopes[outer].kind == "comprehension" {
                        0
                    } else {
                        v.store_after.unwrap_or(n.range.end().to_u32())
                    };
                    self.local(outer, n.id.as_str(), position);
                }
                N::StmtImport(n) => {
                    for alias in &n.names {
                        let name = alias.name.as_str();
                        if let Some(asname) = &alias.asname {
                            self.bind(
                                outer,
                                asname.as_str(),
                                name.to_owned(),
                                alias.range.end().to_u32(),
                            );
                        } else {
                            let mut prefix = String::new();
                            for part in name.split('.') {
                                if !prefix.is_empty() {
                                    prefix.push('.');
                                }
                                prefix.push_str(part);
                                self.bind(
                                    outer,
                                    &prefix,
                                    prefix.clone(),
                                    alias.range.end().to_u32(),
                                );
                            }
                        }
                    }
                }
                N::StmtImportFrom(n) => {
                    let prefix = absolute(
                        self.module,
                        self.file,
                        &format!(
                            "{}{}",
                            ".".repeat(n.level as usize),
                            n.module.as_ref().map_or("", |m| m.as_str())
                        ),
                    );
                    for alias in &n.names {
                        if alias.name.as_str() != "*" {
                            let target = if prefix.is_empty() {
                                alias.name.to_string()
                            } else {
                                format!("{prefix}.{}", alias.name)
                            };
                            self.bind(
                                outer,
                                alias.asname.as_ref().unwrap_or(&alias.name).as_str(),
                                target,
                                alias.range.end().to_u32(),
                            );
                        }
                    }
                }
                N::ExceptHandlerExceptHandler(n) => {
                    if let Some(name) = &n.name {
                        self.local(outer, name.as_str(), name.end().to_u32());
                    }
                }
                N::StmtGlobal(n) => {
                    for name in &n.names {
                        self.scopes[outer].redirects.insert(name.to_string(), 0);
                    }
                }
                N::StmtNonlocal(n) => {
                    for name in &n.names {
                        let mut parent = self.scopes[outer].parent.unwrap_or(0);
                        while self.scopes[parent].kind == "class" {
                            parent = self.scopes[parent].parent.unwrap_or(0);
                        }
                        self.scopes[outer]
                            .redirects
                            .insert(name.to_string(), parent);
                    }
                }
                _ => {}
            }
            // Assign lexical scopes separately from declaration ownership. Decorators,
            // bases, defaults and annotations execute outside the new function/class.
            for child in kids.drain(..).rev() {
                let mut child_scope = inner;
                let mut store_after = v.store_after;
                match v.node {
                    N::StmtAssign(n) if n.targets.iter().any(|t| t.range() == child.range()) => {
                        store_after = Some(n.value.end().to_u32())
                    }
                    N::StmtAnnAssign(n) if n.target.range() == child.range() => {
                        store_after = Some(n.end().to_u32())
                    }
                    N::ExprNamed(n) if n.target.range() == child.range() => {
                        store_after = Some(n.value.end().to_u32())
                    }
                    _ => {}
                }
                match v.node {
                    N::StmtFunctionDef(_) => {
                        if child.as_stmt_ref().is_none() && !matches!(child, N::Parameters(_)) {
                            child_scope = outer;
                        }
                    }
                    N::StmtClassDef(_) => {
                        if child.as_stmt_ref().is_none() {
                            child_scope = outer;
                        }
                    }
                    N::Parameter(_) => child_scope = self.scopes[outer].parent.unwrap_or(outer),
                    N::ParameterWithDefault(_) => {
                        if !matches!(child, N::Parameter(_)) {
                            child_scope = self.scopes[outer].parent.unwrap_or(outer);
                        }
                    }
                    N::Comprehension(n) if child.range() == n.iter.range() => {
                        // Only the first iterable executes in the enclosing scope.
                        let first = self
                            .visits
                            .iter()
                            .rev()
                            .find(|p| p.scope == outer && matches!(p.node, N::Comprehension(_)))
                            .is_none();
                        if first {
                            child_scope = self.scopes[outer].parent.unwrap_or(outer);
                        }
                    }
                    _ => {}
                }
                stack.push(Visit {
                    node: child,
                    scope: child_scope,
                    owner: v.owner.clone(),
                    kind: v.kind,
                    depth: v.depth + 1,
                    store_after,
                });
            }
            self.visits.push(v);
        }
        Ok(())
    }
    fn declaration_location(&self, range: TextRange, name: TextRange, keyword: &str) -> Value {
        let prefix = &self.source[range.start().to_usize()..name.start().to_usize()];
        let start = range.start().to_usize() + prefix.rfind(keyword).unwrap_or(0);
        json!({"start":self.point(start), "end":self.point(range.end().to_usize())})
    }
    fn point(&self, offset: usize) -> Value {
        let line = self
            .lines
            .partition_point(|p| *p <= offset)
            .saturating_sub(1);
        json!({"line":line+1,"column":self.source[self.lines[line]..offset].chars().count()})
    }
    fn location(&self, range: TextRange) -> Value {
        json!({"start":self.point(range.start().to_usize()),"end":self.point(range.end().to_usize())})
    }
    fn names(&self, expr: &Expr, scope: usize) -> Names {
        if let Expr::Name(n) = expr
            && n.ctx == ExprContext::Store
        {
            let target = self.scopes[scope]
                .redirects
                .get(n.id.as_str())
                .copied()
                .unwrap_or(scope);
            return [format!("{}.{}", self.scopes[target].prefix, n.id)]
                .into_iter()
                .collect();
        }
        full_name(expr)
            .map(|name| {
                lookup(
                    &self.scopes,
                    &self.builtins,
                    scope,
                    &name,
                    expr.start().to_u32(),
                    matches!(expr,Expr::Name(n) if n.ctx==ExprContext::Store),
                )
            })
            .unwrap_or_default()
    }
    fn reference(
        &mut self,
        v: &Visit,
        range: TextRange,
        kind: &str,
        candidates: Names,
        hint: Option<&str>,
    ) {
        self.pending.push(json!({"source":v.owner,"kind":kind,"range":self.location(range),
            "start":range.start().to_u32(),"end":range.end().to_u32(),"candidates":candidates,"hint":hint,"receivers":self.receivers}));
    }
    fn assignment(&mut self, target: &Expr, types: Names, scope: usize) {
        for name in self.names(target, scope) {
            self.rebindings.insert(name.clone());
            self.bindings.entry(name).or_default().extend(types.clone());
        }
        if let Expr::Attribute(attr) = target
            && let Expr::Name(n) = attr.value.as_ref()
            && (n.id.as_str() == "self" || n.id.as_str() == "cls")
        {
            for receiver in self.names(&attr.value, scope) {
                if let Some(cls) = self.receivers.get(&receiver) {
                    self.attributes
                        .entry(format!("{cls}.{}", attr.attr))
                        .or_default()
                        .extend(types.clone());
                }
            }
        }
    }
    fn run(&mut self) {
        // The layout pass discovers bindings first, enabling deferred outer-scope lookups.
        for i in 0..self.visits.len() {
            let v = self.visits[i].clone();
            match v.node {
                N::StmtFunctionDef(n) => {
                    let inner = self.child_scopes[&n.start().to_u32()];
                    let staticmethod = n
                        .decorator_list
                        .iter()
                        .any(|d| full_name(&d.expression).as_deref() == Some("staticmethod"));
                    if v.kind == "method"
                        && !staticmethod
                        && let Some(param) = n
                            .parameters
                            .posonlyargs
                            .first()
                            .or(n.parameters.args.first())
                    {
                        let cls = v.owner.rsplit_once('.').unwrap().0;
                        let names = lookup(
                            &self.scopes,
                            &self.builtins,
                            inner,
                            param.parameter.name.as_str(),
                            u32::MAX,
                            true,
                        );
                        for name in names {
                            self.receivers.insert(name, cls.to_owned());
                        }
                    }
                }
                N::StmtClassDef(n) => {
                    if let Some(args) = &n.arguments {
                        for base in &args.args {
                            let value = if let Expr::Subscript(s) = base {
                                s.value.as_ref()
                            } else {
                                base
                            };
                            self.reference(
                                &v,
                                value.range(),
                                "inherits",
                                self.names(value, v.scope),
                                None,
                            );
                        }
                    }
                }
                N::ExprCall(n) => self.reference(
                    &v,
                    n.func.range(),
                    "calls",
                    self.names(&n.func, v.scope),
                    None,
                ),
                N::StmtImport(n) => {
                    for alias in &n.names {
                        self.reference(
                            &v,
                            alias.name.range(),
                            "imports",
                            [alias.name.to_string()].into_iter().collect(),
                            None,
                        );
                        if v.owner == self.module {
                            let name = alias.name.as_str();
                            let local = alias
                                .asname
                                .as_ref()
                                .map_or_else(|| name.split('.').next().unwrap(), |a| a.as_str());
                            let target = if alias.asname.is_some() {
                                name
                            } else {
                                name.split('.').next().unwrap()
                            };
                            self.exports
                                .entry(format!("{}.{local}", self.module))
                                .or_default()
                                .insert(target.to_owned());
                        }
                    }
                }
                N::StmtImportFrom(n) => {
                    let prefix = absolute(
                        self.module,
                        self.file,
                        &format!(
                            "{}{}",
                            ".".repeat(n.level as usize),
                            n.module.as_ref().map_or("", |m| m.as_str())
                        ),
                    );
                    for alias in &n.names {
                        if alias.name.as_str() == "*" {
                            self.reference(
                                &v,
                                n.range,
                                "imports",
                                [prefix.clone()].into_iter().collect(),
                                Some("wildcard import; exported names unknown"),
                            );
                        } else {
                            let target = if prefix.is_empty() {
                                alias.name.to_string()
                            } else {
                                format!("{prefix}.{}", alias.name)
                            };
                            self.reference(
                                &v,
                                alias.name.range(),
                                "imports",
                                [target.clone()].into_iter().collect(),
                                None,
                            );
                            if v.owner == self.module {
                                self.exports
                                    .entry(format!(
                                        "{}.{}",
                                        self.module,
                                        alias.asname.as_ref().unwrap_or(&alias.name)
                                    ))
                                    .or_default()
                                    .insert(target);
                            }
                        }
                    }
                }
                N::Parameter(n) => {
                    if let Some(annotation) = &n.annotation {
                        let outer = self.scopes[v.scope].parent.unwrap_or(v.scope);
                        let types = self.names(annotation, outer);
                        for name in lookup(
                            &self.scopes,
                            &self.builtins,
                            v.scope,
                            n.name.as_str(),
                            u32::MAX,
                            true,
                        ) {
                            self.bindings.entry(name).or_default().extend(types.clone());
                        }
                    }
                }
                N::StmtAnnAssign(n) => {
                    let types = self.names(&n.annotation, v.scope);
                    if v.kind == "class"
                        && let Expr::Name(name) = n.target.as_ref()
                    {
                        self.attributes
                            .entry(format!("{}.{}", v.owner, name.id))
                            .or_default()
                            .extend(types.clone());
                    }
                    self.assignment(&n.target, types, v.scope);
                }
                N::StmtAssign(n) => {
                    let types = if let Expr::Call(c) = n.value.as_ref() {
                        self.names(&c.func, v.scope)
                    } else {
                        self.names(&n.value, v.scope)
                            .iter()
                            .flat_map(|name| self.bindings.get(name).into_iter().flatten().cloned())
                            .collect()
                    };
                    for target in &n.targets {
                        self.assignment(target, types.clone(), v.scope);
                    }
                }
                _ => {}
            }
        }
    }
}

pub fn extract(source: &str, module: &str, file: &str, builtins: Names) -> Result<String, String> {
    let parsed = ruff_python_parser::parse_module(source).map_err(|e| format!("parse: {e}"))?;
    let mut extraction = Extraction {
        source,
        module,
        file,
        scopes: vec![Scope::new(None, module.to_owned(), "module")],
        visits: vec![],
        builtins,
        nodes: vec![],
        pending: vec![],
        exports: NameMap::new(),
        attributes: NameMap::new(),
        bindings: NameMap::new(),
        rebindings: Names::new(),
        receivers: BTreeMap::new(),
        counts: BTreeMap::new(),
        child_scopes: BTreeMap::new(),
        lines: std::iter::once(0)
            .chain(source.match_indices('\n').map(|(p, _)| p + 1))
            .collect(),
    };
    extraction.layout(&parsed.syntax().body)?;
    extraction.run();
    serde_json::to_string(&json!({"nodes":extraction.nodes,"pending":extraction.pending,"exports":extraction.exports,
        "attributes":extraction.attributes,"bindings":extraction.bindings,"rebindings":extraction.rebindings})).map_err(|e|e.to_string())
}
