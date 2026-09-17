//! Bound input complexity before building an AST (whose destructor is recursive).
use ruff_python_ast::token::TokenKind as T;
use ruff_python_parser::{Mode, lexer::lex};

pub fn check(source: &str) -> Result<(), String> {
    if source.len() > 32 * 1024 * 1024 {
        return Err("complexity: source exceeds 32 MiB".to_owned());
    }
    let mut lexer = lex(source, Mode::Module);
    let (mut nesting, mut indentation, mut statement, mut total) = (0usize, 0usize, 0usize, 0usize);
    loop {
        let token = lexer.next_token();
        if token == T::EndOfFile {
            break;
        }
        total += 1;
        statement += 1;
        match token {
            T::Lpar | T::Lsqb | T::Lbrace => nesting += 1,
            T::Rpar | T::Rsqb | T::Rbrace => nesting = nesting.saturating_sub(1),
            T::Indent => indentation += 1,
            T::Dedent => indentation = indentation.saturating_sub(1),
            T::Newline | T::Semi => statement = 0,
            _ => {}
        }
        if nesting > 256 || indentation > 128 || statement > 8192 || total > 1_000_000 {
            return Err("complexity: source exceeds nesting (256), indentation (128), statement token (8192), or file token (1000000) budget".to_owned());
        }
    }
    Ok(())
}
