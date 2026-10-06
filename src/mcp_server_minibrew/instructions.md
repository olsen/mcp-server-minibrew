Reads a MiniBrew account (devices, kegs, sessions, beers, recipes) and creates beers, recipes
and custom ingredients.

Can't do:
- Control hardware. No tool starts, stops or advances a brew, cleaning or keg: the user does that
  on the device or in the app.
- Delete anything. Custom ingredients are permanent: search the catalogue first and confirm name
  and values with the user. Test beers are deleted by the user in the portal.
- create_recipe only works for a beer with no recipe; update_recipe is refused once brewed.

MiniBrew limits:
- Batch 5.5 L. Grain milled coarse (0.7–1.0 mm).
- 1.2–2.3 kg grain per mash stage; above that, split over two mash stages (fits, doesn't raise
  efficiency). Mash steps 40–78 °C. Efficiency 55–70 %, lower with more grain: plan 55–60 %.
- 6 carousel slots: each hop or other boil addition takes one. Max 12 g of hops per slot: split
  more over several slots. Duration = minutes before end of boil; whirlpool hops 0.
- PRIM and COND stages required; SECND optional (closed: carbonation, cold crash). Each extra
  COND stage ends with a trub removal: one per 10 g of dry hops.
- Cold crash at 5 °C: the keg can't cool lower (the API accepts 1 °C).
- chilling_temperature (pitch, default 25 °C) at or above the first PRIM step.
- Serving 5–20 °C. Carbonation on the beer, g/L = volumes × 1.96.

New recipe: use the new_recipe prompt's steps. In short: template from get_recipe, ids from
search_ingredients, calculate water and stats yourself (MiniBrew stores them as sent),
check_recipe, create_beer, create_recipe, then the user saves it once in pro.minibrew.io so the
portal recalculates.

get_device_telemetry only gets data while the device is open in pro.minibrew.io.
