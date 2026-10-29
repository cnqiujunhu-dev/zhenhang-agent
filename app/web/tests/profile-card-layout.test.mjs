import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const css=readFileSync(new URL('../src/customerDataHome.css',import.meta.url),'utf8');
const patch=readFileSync(new URL('../public/profile-cards-equal-20261024.css',import.meta.url),'utf8');
const component=readFileSync(new URL('../src/CustomerDataHome.tsx',import.meta.url),'utf8');
test('standalone deployment stylesheet matches the canonical profile rules',()=>{
  const rules=patch.split('\n').filter(line=>line.startsWith('.customer-data-home'));
  assert.equal(rules.length,7);for(const rule of rules)assert.ok(css.includes(rule),rule);
});
test('both variants stretch in the same grid row without a fixed clipping height',()=>{
  assert.match(patch,/grid-template-rows:subgrid;grid-row:span 2/);
  assert.match(patch,/\.bank-card\{margin:0;min-height:248px;.*align-self:stretch/);
  assert.doesNotMatch(patch,/(?<!min-)height:\s*\d/);
  assert.match(patch,/\.bank-card \.bank-card-bottom\{margin-top:auto/);
});
test('balance facts remain below each card while the duplicate card-face section is hidden',()=>{
  assert.match(component,/card\.balances_by_currency\?\.CNY/);
  assert.match(component,/card\.balances_by_currency\?\.USD/);
  assert.match(patch,/\.dual-card-balances,.*\.dual-card-balance-note\{display:none\}/);
});
test('styles cannot hide balances in other card views or remove identity/status/copy controls',()=>{
  for(const rule of patch.split('\n').filter(line=>line.startsWith('.')))assert.ok(rule.startsWith('.customer-data-home .data-home-card-grid'));
  assert.doesNotMatch(patch,/(?:card-number|state-badge|simulation-card-copy)[^{}]*\{[^}]*display:none/);
});
