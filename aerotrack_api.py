"""AeroTrack - API de consulta de ofertas reais via Duffel.
Requer DUFFEL_ACCESS_TOKEN de ambiente LIVE; não armazene segredo no HTML.
"""
import os
from datetime import date, datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

app = FastAPI(title='AeroTrack API', version='2.0')
origins = [v.strip() for v in os.getenv('ALLOWED_ORIGINS','http://localhost:8000,http://localhost:5500').split(',') if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['POST','GET'], allow_headers=['Content-Type'])

class FlightRequest(BaseModel):
    origin: str = Field(pattern=r'^[A-Z]{3}$')
    destination: str = Field(pattern=r'^[A-Z]{3}$')
    departure_date: date
    return_date: Optional[date] = None
    adults: int = Field(ge=1,le=9)

    @model_validator(mode='after')
    def check_itinerary(self):
        if self.origin==self.destination: raise ValueError('Origem e destino devem ser diferentes')
        if self.departure_date < date.today(): raise ValueError('Data de ida no passado')
        if self.return_date and self.return_date < self.departure_date: raise ValueError('Data de volta anterior à ida')
        return self

@app.get('/health')
def health():
    return {'status':'ok','live_configured':bool(os.getenv('DUFFEL_ACCESS_TOKEN'))}

@app.post('/api/flights')
async def flights(req: FlightRequest):
    token = os.getenv('DUFFEL_ACCESS_TOKEN', '').strip()
    if not token: raise HTTPException(503,'Configure DUFFEL_ACCESS_TOKEN em modo LIVE no servidor')
    slices=[{'origin':req.origin,'destination':req.destination,'departure_date':req.departure_date.isoformat()}]
    if req.return_date:
        slices.append({'origin':req.destination,'destination':req.origin,'departure_date':req.return_date.isoformat()})
    payload={'data':{'slices':slices,'passengers':[{'type':'adult'} for _ in range(req.adults)],'cabin_class':'economy'}}
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            resp=await client.post('https://api.duffel.com/air/offer_requests',params={'return_offers':'true'},json=payload,headers={
                'Authorization':f'Bearer {token}', 'Duffel-Version':'v2', 'Accept':'application/json', 'Content-Type':'application/json'})
        if resp.status_code>=400:
            raise HTTPException(502,f'Provedor de voos retornou HTTP {resp.status_code}. Verifique credenciais e permissão de acesso.')
        data=resp.json().get('data') or {}
    except httpx.HTTPError as exc:
        raise HTTPException(502,f'Falha na conexão com provedor: {type(exc).__name__}') from exc
    # Valores de teste NUNCA devem ser apresentados como ofertas reais.
    if data.get('live_mode') is not True:
        raise HTTPException(503,'O provedor respondeu em modo de teste. Ative credenciais LIVE.')
    now=datetime.now(timezone.utc).isoformat(timespec='minutes')
    offers=[]
    for item in data.get('offers',[]):
        amount=item.get('total_amount')
        if amount is None: continue
        legs=[]; codes=set(); names=[]; max_stops=0
        for sl in item.get('slices',[]):
            segs=sl.get('segments') or []
            if not segs: continue
            nstops=max(len(segs)-1,0);max_stops=max(max_stops,nstops)
            for seg in segs:
                carrier=seg.get('operating_carrier') or seg.get('marketing_carrier') or {}
                code=carrier.get('iata_code')
                name=carrier.get('name')
                if code:codes.add(code)
                if name and name not in names:names.append(name)
            first,last=segs[0],segs[-1]
            legs.append({'from':(first.get('origin') or {}).get('iata_code',''),
                         'to':(last.get('destination') or {}).get('iata_code',''),
                         'departure':first.get('departing_at',''),
                         'arrival':last.get('arriving_at',''),
                         'duration':sl.get('duration',''), 'stops':nstops})
        if not legs: continue
        offers.append({'id':item.get('id'), 'airline':' + '.join(names) or 'Não informada',
            'airline_codes':sorted(codes),'max_stops':max_stops,
            'total_amount':amount,'currency':item.get('total_currency',''),
            'slices':legs,'passengers':req.adults,'cabin':'econômica',
            'expires_at':item.get('expires_at'), 'queried_at':now})
    offers.sort(key=lambda o:float(o['total_amount']))
    return {'live_mode':True,'provider':'Duffel','offers':offers,'queried_at':now}
