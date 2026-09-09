// Direct, fixed, synthetic Payment Charge probe; no flag/configuration access.
const http = require('http');
const grpc = require('/usr/src/app/node_modules/@grpc/grpc-js');
const loader = require('/usr/src/app/node_modules/@grpc/proto-loader');
const api = grpc.loadPackageDefinition(loader.loadSync('/usr/src/app/demo.proto')).oteldemo;
const client = new api.PaymentService('payment:50051', grpc.credentials.createInsecure());
let total = 0, errors = 0, duration = 0, active = false, last = null;
function probe() {
  return new Promise(resolve => {
    const start = performance.now();
    const metadata = new grpc.Metadata();
    metadata.set('baggage', 'synthetic_request=true');
    client.charge({amount: {currencyCode: 'USD', units: 1, nanos: 0},
      creditCard: {creditCardNumber: '4111111111111111', creditCardExpirationYear: 2099, creditCardExpirationMonth: 12}},
      metadata, {deadline: new Date(Date.now() + 5000)}, (err, result) => {
        total++; const ok = !err && typeof result?.transactionId === 'string' && result.transactionId.length > 0;
        if (!ok) errors++;
        const latency = performance.now() - start; duration += latency;
        last = {ok, grpc_code: err?.code ?? 0, expected_payment_failure: !!err?.message?.includes('Payment request failed. Invalid token.'), latency_ms: latency};
        resolve(last);
      });
  });
}
setInterval(async () => {if (!active) {active=true; try {await probe();} finally {active=false;}}}, 250);
http.createServer(async (req, res) => {
  if (req.method !== 'GET') {res.writeHead(405); return res.end();}
  if (req.url === '/probe') {res.setHeader('Content-Type','application/json'); return res.end(JSON.stringify(await probe()));}
  if (req.url === '/metrics') {res.setHeader('Content-Type','text/plain'); return res.end(
    `payment_probe_requests_total{service_name="payment"} ${total}\npayment_probe_errors_total{service_name="payment"} ${errors}\npayment_probe_duration_milliseconds_total{service_name="payment"} ${duration}\n`);}
  if (req.url === '/ready') {res.writeHead(last ? 200 : 503); return res.end(JSON.stringify({ready:!!last}));}
  res.writeHead(404); res.end();
}).listen(8080,'0.0.0.0');
