# Cursor + Polygon Amoy Preflight Checklist

A reference checklist for wiring a local codebase to Polygon Amoy via Alchemy.

---

## 1. Confirm project type

Figure out what your repo is using:

- Hardhat
- Foundry
- Truffle
- Next.js / React frontend only
- Node script with ethers.js or viem
- Full-stack app

Look for files like:

```
hardhat.config.js
hardhat.config.ts
foundry.toml
package.json
scripts/deploy.js
src/
app/
contracts/
```

In Cursor, ask:

> Identify whether this repo uses Hardhat, Foundry, ethers, viem, wagmi, or another Ethereum dev framework.

---

## 2. Create or verify your Alchemy Amoy RPC URL

You want an RPC URL like:

```
https://polygon-amoy.g.alchemy.com/v2/YOUR_KEY
```

Do not paste this directly into source files. Use `.env`.

---

## 3. Create `.env`

In the project root:

```
ALCHEMY_AMOY_RPC_URL=https://polygon-amoy.g.alchemy.com/v2/YOUR_KEY
PRIVATE_KEY=your_test_wallet_private_key_without_0x
```

- Use a **test wallet only**.
- **Never** use your real wallet/private key.

---

## 4. Check `.gitignore`

Make sure `.env` is ignored:

```
.env
.env.local
```

In Cursor, search the repo for:

```
PRIVATE_KEY
ALCHEMY
RPC_URL
```

Make sure no secrets are hardcoded.

---

## 5. Confirm Amoy network settings

```
Network: Polygon Amoy
Chain ID: 80002
Currency: POL
Explorer: https://amoy.polygonscan.com
```

- Mainnet Polygon is **137**.
- Amoy is **80002**.
- This is the most important mistake to avoid.

---

## 6. Install dependencies

For most Node/Hardhat/Ethers projects:

```
npm install dotenv
```

If using Hardhat:

```
npm install --save-dev hardhat @nomicfoundation/hardhat-toolbox
```

If using ethers directly:

```
npm install ethers dotenv
```

If using viem:

```
npm install viem dotenv
```

---

## 7. Add Amoy to Hardhat (if applicable)

In `hardhat.config.js`:

```js
require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

module.exports = {
  solidity: "0.8.24",
  networks: {
    amoy: {
      url: process.env.ALCHEMY_AMOY_RPC_URL,
      accounts: [process.env.PRIVATE_KEY],
      chainId: 80002,
    },
  },
};
```

Then test:

```
npx hardhat compile
```

---

## 8. Add Amoy to Foundry (if applicable)

In `foundry.toml`:

```
[rpc_endpoints]
amoy = "${ALCHEMY_AMOY_RPC_URL}"
```

Then test:

```
source .env
forge build
```

For Windows PowerShell:

```powershell
$env:ALCHEMY_AMOY_RPC_URL="https://polygon-amoy.g.alchemy.com/v2/YOUR_KEY"
$env:PRIVATE_KEY="your_private_key"
forge build
```

---

## 9. Add Amoy to frontend wallet config (if applicable)

If your app uses wagmi/viem, look for chain config.

Example:

```js
import { defineChain } from "viem";

export const polygonAmoy = defineChain({
  id: 80002,
  name: "Polygon Amoy",
  nativeCurrency: {
    decimals: 18,
    name: "POL",
    symbol: "POL",
  },
  rpcUrls: {
    default: {
      http: [process.env.NEXT_PUBLIC_ALCHEMY_AMOY_RPC_URL],
    },
  },
  blockExplorers: {
    default: {
      name: "PolygonScan Amoy",
      url: "https://amoy.polygonscan.com",
    },
  },
});
```

For frontend apps, use:

```
NEXT_PUBLIC_ALCHEMY_AMOY_RPC_URL=https://polygon-amoy.g.alchemy.com/v2/YOUR_KEY
```

> Anything with `NEXT_PUBLIC_` is visible in the browser. Only use it for public RPC URLs, never private keys.

---

## 10. Run a basic RPC smoke test

Create `scripts/check-amoy.js`:

```js
require("dotenv").config();
const { ethers } = require("ethers");

async function main() {
  const provider = new ethers.JsonRpcProvider(
    process.env.ALCHEMY_AMOY_RPC_URL
  );

  const network = await provider.getNetwork();
  console.log("Chain ID:", network.chainId.toString());

  const block = await provider.getBlockNumber();
  console.log("Latest block:", block);

  if (network.chainId.toString() !== "80002") {
    throw new Error("Wrong network. Expected Polygon Amoy chain ID 80002.");
  }

  console.log("Amoy RPC is working.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

Run:

```
node scripts/check-amoy.js
```

Expected output:

```
Chain ID: 80002
Latest block: <some number>
Amoy RPC is working.
```

---

## 11. Check wallet balance

Add this to the smoke test:

```js
const wallet = new ethers.Wallet(process.env.PRIVATE_KEY, provider);
const balance = await provider.getBalance(wallet.address);

console.log("Wallet:", wallet.address);
console.log("Balance:", ethers.formatEther(balance), "POL");
```

You want a non-zero POL balance before deploying.

---

## 12. Compile before deploying

For Hardhat:

```
npx hardhat compile
```

For Foundry:

```
forge build
```

Do not deploy until compile/build passes cleanly.

---

## 13. Run tests locally first

Hardhat:

```
npx hardhat test
```

Foundry:

```
forge test
```

Fix local test failures before pushing to Amoy.

---

## 14. Deploy to Amoy

Hardhat:

```
npx hardhat run scripts/deploy.js --network amoy
```

Foundry:

```
forge script script/Deploy.s.sol \
  --rpc-url amoy \
  --private-key $PRIVATE_KEY \
  --broadcast
```

PowerShell version:

```powershell
forge script script/Deploy.s.sol `
  --rpc-url $env:ALCHEMY_AMOY_RPC_URL `
  --private-key $env:PRIVATE_KEY `
  --broadcast
```

---

## 15. Verify deployment

After deploying, confirm:

- [ ] Contract address exists
- [ ] Transaction appears on amoy.polygonscan.com
- [ ] Wallet paid gas in test POL
- [ ] App points to chain ID 80002
- [ ] Frontend wallet prompts for Polygon Amoy

---

## Cursor prompt you can paste

```
Inspect this repo and help me wire it to Polygon Amoy using Alchemy.

Preflight goals:
1. Identify whether this project uses Hardhat, Foundry, ethers.js, viem, wagmi, or another framework.
2. Find all network/RPC configuration files.
3. Add Polygon Amoy config with chain ID 80002.
4. Use environment variables instead of hardcoded RPC URLs or private keys.
5. Make sure .env is ignored by git.
6. Add or update a smoke test that checks the RPC connection and confirms chain ID 80002.
7. Do not modify unrelated code.
8. Explain each change before applying it.
```

---

## Minimum safe checklist

Before you deploy, these should all be true:

- [ ] I am using a test wallet
- [ ] `.env` contains my Alchemy Amoy RPC URL
- [ ] `.env` contains my test wallet private key
- [ ] `.env` is in `.gitignore`
- [ ] Chain ID is 80002
- [ ] Gas token is POL
- [ ] Wallet has test POL
- [ ] RPC smoke test passes
- [ ] Local compile/build passes
- [ ] Local tests pass
- [ ] Deployment script is pointed at Amoy, not mainnet

---

## Two biggest things to avoid

1. Using your real wallet.
2. Accidentally deploying to Polygon mainnet (137) instead of Amoy (80002).
