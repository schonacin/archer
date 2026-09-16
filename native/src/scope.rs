//! Archer's lexical names; no Ruff semantic-model types escape this module.
use ruff_python_ast::Expr;
use std::collections::{BTreeMap, BTreeSet};

pub type Names = BTreeSet<String>;
pub type NameMap = BTreeMap<String, Names>;

#[derive(Clone)]
pub struct Binding {
    pub name: String,
    pub position: u32,
}

pub struct Scope {
    pub parent: Option<usize>,
    pub prefix: String,
    pub kind: &'static str,
    pub bindings: BTreeMap<String, Vec<Binding>>,
    pub redirects: BTreeMap<String, usize>,
}
impl Scope {
    pub fn new(parent: Option<usize>, prefix: String, kind: &'static str) -> Self {
        Self {
            parent,
            prefix,
            kind,
            bindings: BTreeMap::new(),
            redirects: BTreeMap::new(),
        }
    }
}

pub fn full_name(mut expr: &Expr) -> Option<String> {
    let mut suffix = Vec::new();
    loop {
        match expr {
            Expr::Name(n) => {
                suffix.push(n.id.to_string());
                suffix.reverse();
                return Some(suffix.join("."));
            }
            Expr::Attribute(n) => {
                suffix.push(n.attr.to_string());
                expr = &n.value;
            }
            Expr::Call(n) => expr = &n.func,
            Expr::Subscript(n) => expr = &n.value,
            Expr::NoneLiteral(_) => {
                suffix.push("None".to_owned());
                suffix.reverse();
                return Some(suffix.join("."));
            }
            Expr::BooleanLiteral(n) => {
                suffix.push(if n.value { "True" } else { "False" }.to_owned());
                suffix.reverse();
                return Some(suffix.join("."));
            }
            _ => {
                // Preserve LibCST's full-name helper behavior for attributes on
                // literals/compound expressions, including its "None" prefix.
                if suffix.is_empty() {
                    return None;
                }
                suffix.push("None".to_owned());
                suffix.reverse();
                return Some(suffix.join("."));
            }
        }
    }
}

pub fn absolute(module: &str, file: &str, name: &str) -> String {
    let level = name.bytes().take_while(|c| *c == b'.').count();
    if level == 0 {
        return name.to_owned();
    }
    let package = if file == "__init__.py" || file.ends_with("/__init__.py") {
        module
    } else {
        module.rsplit_once('.').map_or("", |(p, _)| p)
    };
    let mut parts: Vec<&str> = if package.is_empty() {
        vec![]
    } else {
        package.split('.').collect()
    };
    let keep = if level <= parts.len() {
        parts.len() - level + 1
    } else {
        0
    };
    parts.truncate(keep);
    if name.len() > level {
        parts.push(&name[level..]);
    }
    parts.join(".")
}

pub fn lookup(
    scopes: &[Scope],
    builtins: &Names,
    scope: usize,
    name: &str,
    position: u32,
    store: bool,
) -> Names {
    let mut current = Some(scope);
    let mut deferred = false;
    while let Some(index) = current {
        let s = &scopes[index];
        if let Some(&target) = s.redirects.get(name.split('.').next().unwrap_or(name))
            && target != index
        {
            current = Some(target);
            deferred = true;
            continue;
        }
        let mut prefix = name;
        loop {
            if let Some(bindings) = s.bindings.get(prefix) {
                let found: Names = bindings
                    .iter()
                    .filter(|b| store || deferred || b.position <= position)
                    .map(|b| format!("{}{}", b.name, &name[prefix.len()..]))
                    .collect();
                if !found.is_empty() || s.kind != "class" {
                    return found;
                }
                break;
            }
            match prefix.rsplit_once('.') {
                Some((p, _)) => prefix = p,
                None => break,
            }
        }
        deferred |= s.kind == "function" || s.kind == "lambda";
        current = s.parent;
        // Class namespaces are not closures for methods or nested classes.
        while let Some(parent) = current {
            if scopes[parent].kind != "class" {
                break;
            }
            current = scopes[parent].parent;
        }
    }
    let root = name.split('.').next().unwrap_or(name);
    if builtins.contains(root) {
        [format!("builtins.{root}")].into_iter().collect()
    } else {
        Names::new()
    }
}
