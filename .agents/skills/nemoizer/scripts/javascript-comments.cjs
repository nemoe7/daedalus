'use strict';
const fs = require('node:fs');
const { parse } = require('acorn');
const source = fs.readFileSync(0, 'utf8');
for (const sourceType of ['module', 'script']) {
  const comments = [];
  try {
    parse(source, {
      ecmaVersion: 'latest',
      sourceType,
      allowHashBang: true,
      allowReturnOutsideFunction: true,
      allowAwaitOutsideFunction: true,
      locations: true,
      onComment: (block, text, start, end, first, last) => {
        if (start === 0 && source.startsWith('#!')) return;
        comments.push([text, first.line, last.line]);
      },
    });
    process.stdout.write(JSON.stringify(comments));
    break;
  } catch (error) {
    if (!(error instanceof SyntaxError) || sourceType === 'script') throw error;
  }
}
