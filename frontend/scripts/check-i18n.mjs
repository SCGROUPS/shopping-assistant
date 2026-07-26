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
    else if (entry.endsWith('.tsx')) out.push(full)
  }
  return out
}

const findings = []
let operatorSkipped = 0

for (const file of walkFiles(SRC)) {
  const relativePath = relative(ROOT, file)
  const operatorOnly = OPERATOR_ONLY.has(relativePath)
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )

  const report = (node, text) => {
    if (operatorOnly) {
      operatorSkipped += 1
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
      const strings = []
      const collect = (expression) => {
        if (
          ts.isStringLiteral(expression) ||
          ts.isNoSubstitutionTemplateLiteral(expression)
        ) {
          strings.push(expression)
        } else if (ts.isTemplateExpression(expression)) {
          // `Ask Mai about ${title}` - a sentence with a hole in it. The
          // English lives in the literal spans, so it needs a dictionary key
          // with a placeholder, not a template.
          if (!isAllowed(expression.head.text)) strings.push(expression.head)
          for (const span of expression.templateSpans) {
            if (!isAllowed(span.literal.text)) strings.push(span.literal)
          }
        } else if (ts.isConditionalExpression(expression)) {
          collect(expression.whenTrue)
          collect(expression.whenFalse)
        }
      }
      collect(node.expression)
      for (const literal of strings) {
        if (!isAllowed(literal.text)) report(literal, literal.text)
      }
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

const skipNote = operatorSkipped
  ? ` (${operatorSkipped} operator-console string(s) skipped by policy)`
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
