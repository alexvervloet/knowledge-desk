# Records demo.gif against the live deployment, so every answer in the gif is a
# real Claude call over real retrieval, not a mock.
#
#     vhs demo.tape
#
# The point of the recording is the contrast: the same question, two tenants.
# Acme's document answers it. Globex retrieves only its own documents and the
# assistant refuses rather than reaching for the model's own knowledge, which is
# the permission boundary holding all the way through to the generated text.

Output demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1000
Set Height 400
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 28ms

# Warm the machine first: it sleeps when idle and a cold start is not the story.
Hide
Type "curl -fsS https://knowledge-desk.fly.dev/healthz > /dev/null" Enter
Sleep 6s
Type "clear" Enter
Show

Type "# Two tenants. Same question. Same deployment." Enter
Sleep 1500ms

Type `./demo/ask.sh acme "how long do refunds take?"` Enter
Sleep 9s

Type "# Now the other tenant asks the identical question." Enter
Sleep 1500ms

Type `./demo/ask.sh globex "how long do refunds take?"` Enter
Sleep 11s

Type "# Globex never saw Acme's handbook. Enforced in the SQL that" Enter
Type "# fetches candidates, and again by Postgres row-level security." Enter
Sleep 3s
