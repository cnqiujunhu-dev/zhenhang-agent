import assert from "node:assert/strict";
import {after,test} from "node:test";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

const server=await createServer({root:fileURLToPath(new URL("../",import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false},esbuild:{jsx:"automatic"}});
after(()=>server.close());
const {StructuredData}=await server.ssrLoadModule("/src/components.tsx");
const {CustomerLanguageScope}=await server.ssrLoadModule("/src/CustomerLanguageProvider.tsx");
const render=(data,locale="zh-CN")=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},
  React.createElement(StructuredData,{data,confirmation:true})));
const original={transfer_channel:"INTERNAL_BANK",payee_name:"zhenhangd18b0a0f",payee_last4:"6387",
  recipient_username:"recording_family",recipient_display_name:"林悦",amount_minor:1234,currency:"CNY",
  source_card_number:"zhenhang12349a62",source_card_display_last4:"9a62",
  recipient_card_number:"zhenhangd18b0a0f",recipient_card_display_last4:"0a0f",memo:"材料费"};

for(const locale of ["zh-CN","en"])test(`confirmation shows verified person and selected card ends, not internal account suffix as a card: ${locale}`,()=>{
  const data=structuredClone(original),before=JSON.stringify(data),html=render(data,locale);
  const [main,details]=html.split('<details class="confirmation-field-details">');
  assert.match(main,locale==="en"?/<dt>Recipient<\/dt><dd>林悦/:/<dt>收款人<\/dt><dd>林悦/);
  assert.match(main,/9a62/);assert.match(main,/0a0f/);assert.match(main,/12\.34/);assert.match(main,/材料费/);
  assert.doesNotMatch(main,/6387|recording_family|zhenhangd18b0a0f/);
  assert.match(details,locale==="en"?/Internal account identifier ending/:/内部账户标识尾码/);
  assert.match(details,/6387|recording_family/);
  assert.match(details,locale==="en"?/Recipient reference/:/收款标识/);
  assert.equal(JSON.stringify(data),before,"Display cannot rewrite signed confirmation fields");
});

test("no display name does not label a submitted card number as a person",()=>{
  const data={...original};delete data.recipient_display_name;
  const html=render(data),[main,details]=html.split('<details class="confirmation-field-details">');
  assert.match(main,/<dt>收款标识<\/dt>/);assert.match(main,/recording_family/);
  assert.doesNotMatch(main,/<dt>收款人<\/dt>|6387/);assert.match(details,/6387/);
});

test("external recipient and suffix stay first-class; conflicting card snapshots stay visible",()=>{
  const external=render({transfer_channel:"EXTERNAL_SIM",payee_name:"房东",payee_last4:"6678",amount_minor:1234,currency:"CNY"});
  const [main]=external.split('<details class="confirmation-field-details">');
  assert.match(main,/<dt>收款人<\/dt><dd>房东/);assert.match(main,/6678/);
  const conflict=render({...original,recipient_card_display_last4:"ffff"});
  assert.match(conflict,/0a0f/);assert.match(conflict,/ffff/);
});
