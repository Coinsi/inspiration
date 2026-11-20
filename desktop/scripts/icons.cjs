'use strict';
const sharp = require('sharp');
const fs = require('node:fs/promises');
const path = require('node:path');
async function main() {
  const build = path.join(__dirname, '../build');
  const source = path.join(build, 'icon.svg');
  await sharp(source).resize(1024).png().toFile(path.join(build, 'icon.png'));
  const png = await sharp(source).resize(256).png().toBuffer();
  const ico = Buffer.alloc(22);
  ico.writeUInt16LE(1, 2); ico.writeUInt16LE(1, 4);
  ico.writeUInt16LE(1, 10); ico.writeUInt16LE(32, 12);
  ico.writeUInt32LE(png.length, 14); ico.writeUInt32LE(22, 18);
  await fs.writeFile(path.join(build, 'icon.ico'), Buffer.concat([ico, png]));
  const chunks = [];
  for (const [type, size] of [['icp4', 16], ['icp5', 32], ['icp6', 64], ['ic07', 128], ['ic08', 256], ['ic09', 512], ['ic10', 1024]]) {
    const data = await sharp(source).resize(size).png().toBuffer();
    const header = Buffer.alloc(8); header.write(type); header.writeUInt32BE(data.length + 8, 4);
    chunks.push(header, data);
  }
  const header = Buffer.alloc(8); header.write('icns');
  header.writeUInt32BE(8 + chunks.reduce((sum, chunk) => sum + chunk.length, 0), 4);
  await fs.writeFile(path.join(build, 'icon.icns'), Buffer.concat([header, ...chunks]));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
