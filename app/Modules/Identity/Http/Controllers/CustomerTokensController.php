<?php

declare(strict_types=1);

namespace App\Modules\Identity\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Models\User;
use App\Modules\Api\Auth\ApiTokens;
use App\Modules\Api\Auth\TokenAbilities;
use App\Modules\Audit\Action;
use App\Modules\Audit\ActivityLogger;
use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;
use Inertia\Inertia;
use Inertia\Response;
use Laravel\Sanctum\PersonalAccessToken;

/**
 * Customer tokens — the web twin of POST /api/v1/tokens for an issuer.
 *
 * An issuer is a staff account holding the create_api_tokens permission
 * (e.g. a diagnostics pipeline that hands each customer a throwaway upload
 * token). This screen lets that issuer do, from the browser, exactly what
 * the API endpoint does: see the status of the tokens they have minted and
 * mint new ones.
 *
 * Security, all of it inherited from the installation rather than reinvented:
 *
 *  - **Gated** — the routes carry `can:create_api_tokens` (see
 *    routes/settings.php), so only an issuer reaches this screen at all.
 *  - **Scoped** — every read and mutation is bound to the caller's own
 *    tokens through the `tokens()` relation, the same rule
 *    ApiTokensController enforces. A miss is a 404, not a 403, so ids cannot
 *    be probed for existence.
 *  - **No chaining** — store() validates against TokenAbilities::grantableFor,
 *    which is the issuer's own abilities minus create_api_tokens. A minted
 *    token can therefore never mint tokens of its own; the chain stops here.
 *  - **Re-proven** — minting and revoking sit behind password.confirm, for
 *    the same reason the personal API-tokens screen does: a token outlives
 *    the session that minted it, so a stolen session must not be enough.
 *  - **Audited** — both ends of a token's life land in the activity log via
 *    the same Action cases the rest of the app uses.
 *
 * The minted token belongs to the issuer (the owner), exactly as on the API
 * endpoint: the customer is the holder of the secret, not a separate account.
 */
class CustomerTokensController extends Controller
{
    public function __construct(
        private readonly TokenAbilities $abilities,
        private readonly ActivityLogger $activity,
    ) {}

    /**
     * The status of every token this issuer holds, most recent first.
     */
    public function index(Request $request): Response
    {
        $user = $request->user();
        assert($user !== null);

        return Inertia::render('settings/customer-tokens/index', [
            'tokens' => $this->tokensFor($user),
            // Flashed by store() and never persisted: the one and only time
            // the plaintext exists outside the caller's clipboard. The
            // database holds a SHA-256 hash.
            'created_token' => $request->session()->get('created_customer_token'),
        ]);
    }

    /**
     * The minting form. The abilities offered are the no-chaining ceiling,
     * so create_api_tokens is never a checkbox here.
     */
    public function create(Request $request): Response
    {
        $user = $request->user();
        assert($user !== null);

        return Inertia::render('settings/customer-tokens/create', [
            'available_abilities' => $this->availableAbilities($user),
            'defaults' => [
                'expires_in_days' => (int) config('api.tokens.default_days'),
                'max_days' => (int) config('api.tokens.max_days'),
            ],
        ]);
    }

    public function store(Request $request): RedirectResponse
    {
        $user = $request->user();
        assert($user !== null);

        // The ceiling on what a minted token may do is the issuer's own
        // permission set at this moment, minus create_api_tokens — the
        // no-chaining rule. EnsureTokenCan re-checks the intersection on
        // every request in case the role changes later.
        $grantable = $this->abilities->grantableFor($user);
        $maxDays = (int) config('api.tokens.max_days');

        $validated = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'abilities' => ['required', 'array', 'min:1'],
            'abilities.*' => ['string', Rule::in($grantable)],
            'never_expires' => ['boolean'],
            'expires_in_days' => [
                Rule::requiredIf(fn (): bool => ! $request->boolean('never_expires')),
                'nullable', 'integer', 'min:1', 'max:'.$maxDays,
            ],
        ]);

        $expiresAt = $request->boolean('never_expires')
            ? null
            : now()->addDays((int) $validated['expires_in_days']);

        $token = $user->createToken(
            $validated['name'],
            array_values(array_unique($validated['abilities'])),
            $expiresAt,
        );

        $this->activity->log(Action::ApiTokenCreated, $user, context: [
            'token_name' => $validated['name'],
            'abilities' => $validated['abilities'],
            'expires_at' => $expiresAt?->toIso8601String(),
        ]);

        return redirect()->route('customer-tokens.index')->with('created_customer_token', [
            'name' => $validated['name'],
            'plain_text' => $token->plainTextToken,
        ]);
    }

    public function destroy(Request $request, string $token): RedirectResponse
    {
        $user = $request->user();
        assert($user !== null);

        $accessToken = $this->findOwnToken($user, $token);

        $this->activity->log(Action::ApiTokenRevoked, $user, context: [
            'token_name' => $accessToken->name,
        ]);

        $accessToken->delete();

        return back();
    }

    /**
     * Scoped to the caller's own tokens: the relation is what enforces it —
     * a bare PersonalAccessToken::find() would let an issuer revoke or read
     * somebody else's integration by guessing an id. A miss is a 404 rather
     * than a 403, so ids cannot be probed for existence.
     */
    private function findOwnToken(User $user, string $token): PersonalAccessToken
    {
        $accessToken = $user->tokens()->whereKey($token)->first();

        abort_unless($accessToken instanceof PersonalAccessToken, 404);

        return $accessToken;
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function tokensFor(User $user): array
    {
        return array_values($user->tokens()
            ->orderByDesc('created_at')
            ->get()
            ->map(fn (PersonalAccessToken $token): array => [
                'id' => (string) $token->getKey(),
                'name' => $token->name,
                'abilities' => $token->abilities ?? [],
                'last_used_at' => $token->last_used_at?->toIso8601String(),
                'expires_at' => $token->expires_at?->toIso8601String(),
                'expired' => ! ApiTokens::isActive($token),
                'created_at' => $token->created_at?->toIso8601String(),
            ])
            ->all());
    }

    /**
     * The no-chaining ceiling, grouped by category so the form reads like
     * the roles screen rather than a flat wall of checkboxes.
     *
     * @return list<array{category: string, label: string, abilities: list<array{key: string, label: string}>}>
     */
    private function availableAbilities(User $user): array
    {
        $groups = [];

        foreach ($this->abilities->grantableCasesFor($user) as $permission) {
            $groups[$permission->category()->value]['label'] = $permission->category()->label();
            $groups[$permission->category()->value]['abilities'][] = [
                'key' => $permission->value,
                'label' => $permission->label(),
            ];
        }

        return array_map(
            static fn (string $category, array $group): array => [
                'category' => $category,
                'label' => $group['label'],
                'abilities' => $group['abilities'],
            ],
            array_keys($groups),
            $groups,
        );
    }
}
