// Finds shopper-facing English that never reached the dictionary.
//
// This exists because the thing it replaces did not work. `UNEXTRACTED_SURFACES`
// was a hand-maintained list of components still holding literals, and a
// coverage number computed against it read 100% while the checkout was
// entirely English - the list was an assertion, and assertions rot silently.
// A locale is only offered when this check passes, so the claim "the interface
// speaks Vietnamese" is now verified by walking the syntax tree rather than by
// remembering to update a constant.
import ts from 'typescript'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname
const ROOT = new URL('..', import.meta.url).pathname

// Attributes whose values a shopper reads or hears. `alt`, `aria-label`,
// `title` and `placeholder` are text; everything else on a DOM element is
// machinery. `label` is here because components take it as a prop and pass it
// straight to `aria-label` - the string is just as visible for being one
// indirection away.
const TEXT_ATTRIBUTES = new Set([
  'alt',
  'aria-label',
  'label',
  'title',
  'placeholder',
])

// Object properties whose values a shopper reads. A string only has to be
// written *somewhere* to be rendered later: the nudges lived in `presence.ts`,
// the assistant's replies in `api.ts` and the welcome line in a module-level
// constant, and all of them reached the screen while a check that read only
// markup reported the interface fully translated. Matching on the property
// name catches the string where it is written instead.
//
// `raw` is here on purpose. `LocalizedText` accepts `{ raw }` for prose the
// assistant service wrote in the shopper's language, so a literal in that
// position is English being carried past the type.
const TEXT_PROPERTIES = new Set([
  'alt',
  'ariaLabel',
  'caption',
  'description',
  'heading',
  'hint',
  'label',
  'message',
  'opener',
  'placeholder',
  'prompt',
  'raw',
  'summary',
  'text',
  'title',
])

// Sample catalogue content, not interface chrome. In production every one of
// these fields is served from the database already translated, so the English
// here is a fixture standing in for supplier data rather than a string the
// application is responsible for translating.
const CONTENT_FIXTURES = new Set(['src/data/demo.ts'])

// Operator tooling, not storefront. The console is used by staff who author in
// Vietnamese or English and it has its own language policy; the shopper-facing
// chrome gate does not speak for it. Listed explicitly, and the number of
// strings skipped is printed on every run, so this cannot quietly grow to
// cover a surface shoppers actually see.
const OPERATOR_ONLY = new Set(['src/components/AdminConsole.tsx'])

// Strings that are the same in every language. Proper nouns, brand marks and
// punctuation carry no translation obligation. Kept deliberately short: an
// allowlist is the escape hatch that quietly becomes the rule.
const ALLOWED = new Set(['VIETRA', 'Vietra', 'V'])

const isAllowed = (text) => {
  const trimmed = text.trim()
  if (!trimmed) return true
  if (ALLOWED.has(trimmed)) return true
  // No letters means nothing to translate: separators, counts, currency codes.
  if (!/\p{Letter}/u.test(trimmed)) return true
  return false
}

const walkFiles = (dir, out = []) => {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walkFiles(full, out)
    else if (entry.endsWith('.tsx') || entry.endsWith('.ts')) out.push(full)
  }
  return out
}

// Pulls the translatable literals out of an expression, following the shapes
// English actually hides in: a plain string, a template with substitutions
// (`Ask Mai about ${title}` - the sentence is in the spans), and both arms of
// a ternary, which is what a half-translated line looks like.
const collectText = (expression, emit) => {
  if (!expression) return
  if (
    ts.isStringLiteral(expression) ||
    ts.isNoSubstitutionTemplateLiteral(expression)
  ) {
    if (!isAllowed(expression.text)) emit(expression)
  } else if (ts.isTemplateExpression(expression)) {
    if (!isAllowed(expression.head.text)) emit(expression.head)
    for (const span of expression.templateSpans) {
      if (!isAllowed(span.literal.text)) emit(span.literal)
    }
  } else if (ts.isConditionalExpression(expression)) {
    collectText(expression.whenTrue, emit)
    collectText(expression.whenFalse, emit)
  }
}

const findings = []
let operatorSkipped = 0
let fixtureSkipped = 0

for (const file of walkFiles(SRC)) {
  const relativePath = relative(ROOT, file)
  const operatorOnly = OPERATOR_ONLY.has(relativePath)
  const fixture = CONTENT_FIXTURES.has(relativePath)
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  )

  const report = (node, text) => {
    if (operatorOnly) {
      operatorSkipped += 1
      return
    }
    if (fixture) {
      fixtureSkipped += 1
      return
    }
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source))
    findings.push({
      file: relativePath,
      line: line + 1,
      text: text.trim().replace(/\s+/g, ' ').slice(0, 60),
    })
  }

  const visit = (node) => {
    // Text sitting directly in markup: <span>Save voucher</span>
    if (ts.isJsxText(node) && !isAllowed(node.text)) {
      report(node, node.text)
    }

    // A bare string inside braces used as element *content*:
    // <span>{'Save voucher'}</span>, and the string arms of a ternary, which
    // is how half-translated components usually look:
    // {voucher ? 'Booked' : t('checkout.review')}
    //
    // Only when the container is a child of an element. The same syntax in an
    // attribute is usually className={busy ? 'active' : ''} - machinery, not
    // language - and attribute text is handled by TEXT_ATTRIBUTES below.
    if (
      ts.isJsxExpression(node) &&
      node.expression &&
      node.parent &&
      (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))
    ) {
      collectText(node.expression, (literal) => report(literal, literal.text))
    }

    // A text-bearing property in an object literal:
    //   { label: 'Questions before you book?' }
    // regardless of which file it sits in or how far it travels before it is
    // rendered. Also covers `{ raw: 'English' }`, which would otherwise be a
    // hole straight through `LocalizedText`.
    if (
      ts.isPropertyAssignment(node) &&
      (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) &&
      TEXT_PROPERTIES.has(node.name.text)
    ) {
      collectText(node.initializer, (literal) =>
        report(literal, literal.text),
      )
    }

    // Attribute values a shopper reads: aria-label="Close"
    if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name)) {
      const name = node.name.text
      if (TEXT_ATTRIBUTES.has(name) && node.initializer) {
        const initializer = node.initializer
        if (ts.isStringLiteral(initializer) && !isAllowed(initializer.text)) {
          report(initializer, initializer.text)
        }
        if (ts.isJsxExpression(initializer) && initializer.expression) {
          const expression = initializer.expression
          if (ts.isStringLiteral(expression) && !isAllowed(expression.text)) {
            report(expression, expression.text)
          }
          if (ts.isTemplateExpression(expression)) {
            if (!isAllowed(expression.head.text)) {
              report(expression.head, expression.head.text)
            }
            for (const span of expression.templateSpans) {
              if (!isAllowed(span.literal.text)) {
                report(span.literal, span.literal.text)
              }
            }
          }
        }
      }
    }

    ts.forEachChild(node, visit)
  }

  visit(source)
}

const skipped = [
  operatorSkipped ? `${operatorSkipped} operator-console` : '',
  fixtureSkipped ? `${fixtureSkipped} demo-fixture` : '',
].filter(Boolean)
const skipNote = skipped.length
  ? ` (${skipped.join(', ')} string(s) skipped by policy)`
  : ''

if (findings.length) {
  console.error(
    `${findings.length} shopper-facing string(s) are not translatable.${skipNote}\n` +
      'Move each into src/lib/i18n.ts and render it with t(...):\n',
  )
  for (const finding of findings) {
    console.error(`  ${finding.file}:${finding.line}  "${finding.text}"`)
  }
  process.exit(1)
}

console.log(
  `i18n: every shopper-facing string is translatable.${skipNote}`,
)
