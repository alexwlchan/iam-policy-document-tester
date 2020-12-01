# iam-policy-document-tester

**Create short-lived, temporary roles for experimenting with AWS IAM policy documents.**

This is a Python function for rapidly testing and experimenting with AWS IAM policy documents.
Here's what it looks like:

```python
with temporary_iam_credentials(admin_role_arn, policy_document) as credentials:
    # Do stuff with your new credentials, which have the permissions defined by the
    # IAM policy document.
```

The function `temporary_iam_credentials()` gives you a set of temporary AWS credentials, which have the permissions defined by the IAM policy document.
You can make API calls using those credentials, and check they behave correctly -- that API calls are allowed or denied as appropriate.
When you're done, it cleans up after itself, so there are no temporary roles or users left hanging around in your account.

This dramatically speeds up the flow for developing IAM policy documents.
It gives me a fast write-test-debug loop for making changes; much faster than if I was using a more full-featured deployment tool like Terraform or CloudFormation.

**Epistemic status:** lightly tested, shared as an interesting experiment rather than something you should rely on.


## How does it work?

The function creates a temporary IAM role, and attaches your policy document as an inline policy.
(I considered creating a temporary IAM user, but roles have [a 5x limit on the size of inline policies](https://aws.amazon.com/premiumsupport/knowledge-center/iam-increase-policy-size/).)

Then it gives your admin role permission to assume the temporary role, assumes it, and gets some credentials using STS.
It hands back those credentials for you to use.

When you're done, it cleans up the temporary role, so there's nothing left hanging around in your account.



## Interesting ideas: what did I learn?

Here are some of the interesting things I learnt while writing this code:

*   **Context managers are great for temporary resources.**
    Context managers are a useful Python feature that let you create a resource, and ensure it gets cleaned up afterwards.
    An example you've probably used is the `open` function for files:

    ```python
    with open('spam.txt', 'r') as f:
        print(f.read())
    ```

    The file will be closed when you're done, even if an exception is thrown inside the `with` block.

    I'm using [contextlib.contextmanager](https://docs.python.org/3/library/contextlib.html#contextlib.contextmanager) to create a couple of my own context managers for temporary IAM resources, so those resources can always be cleaned up afterwards.
    It goes something like:

    ```python
    import contextlib

    @contextlib.contextmanager
    def temporary_iam_resource(*args, **kwargs):
        # Code to create resource
        resource = create_iam_resource(*args, **kwargs)
        try:
            yield resource
        finally:
            # Code to clean up resource
            delete_iam_resource(resource)
    ```

*   **ExitStack is a good way to handle nested context managers.**
    This script creates several temporary resources, and the nested context managers start to get unwieldy:

    ```python
    with temporary_iam_role() as role1:
        with temporary_iam_role() as role2:
            with temporary_iam_role_policy(role1, policy_document):
                with temporary_iam_role_policy(role2, another_policy_document):
                    ...
    ```

    I recently read about ExitStack in [a blog post by Nikolaus Rath](https://www.rath.org/on-the-beauty-of-pythons-exitstack.html), which gives a way to nest context managers in a cleaner way:

    ```python
    with contextlib.ExitStack() as es:
        role1 = es.enter_context(temporary_iam_role())
        role2 = es.enter_context(temporary_iam_role())
        es.enter_context(temporary_iam_role_policy(role1, policy_document))
        es.enter_context(temporary_iam_role_policy(role2, another_policy_document))
    ```

    Not only does it reduce the amount of indentation, it also lines things up vertically so it's easier to see the similarities between different lines.

*   **Changes in IAM take a while to propagate.**
    In particular:
    
    *    There's a delay between creating a role and being able to assume it
    *    There's a delay between creating a role, and credentials that use that role being usable.
    
    I have a hard-coded 15 second delay in my script, because that's what it took in my testing.
    This code is meant for experiments or one-off actions, not somethign you should rely on in production.

    These delays aren't a surprise -- IAM is a global, distributed system, and changes won't propagate instantly -- but it's the first time I've encountered it, because I don't usually create roles and immediately try to use them.

*   **You can use EC2's [DescribeRegions API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeRegions.html) to test IAM credentials.**
    The API call has a `DryRun` flag, which tells you if the request was authorised without actually making it, and I saw several examples that suggested using it.

    I did try it here, but it wasn't a reliable source of *"is this role ready yet?"*
    Sometimes a DescribeRegions call would succeed, then the next call would fail, then the next call would succeed.
    Consistency in distributed systems is hard.



## Motivation: why did I write this?

I work on an [archival storage service](https://stacks.wellcomecollection.org/building-wellcome-collections-new-archival-storage-service-3f68ff21927e), which keeps a copy of every object in two S3 buckets (our "permanent storage").
It's important that objects in these buckets are never inadvertently modified or deleted.

Developers have several IAM roles that we use, which give us different permissions within the account (e.g. *read-only*, *billing*, *developer*, *admin*).
Although the latter two roles can usually do almost anything in an account, we have [a blanket "Deny" rule](https://github.com/wellcomecollection/storage-service/blob/95e56ae99498e7f6f8d4a3cb430ba4c318d6f645/terraform/critical_prod/delete_protection.tf#L51-L76) that prevents those roles from modifying anything in these permanent storage buckets -- so we can't corrupt the archive by accident.

However, sometimes we do want to delete objects -- for example, objects that were stored in the wrong place.

When weI do this, I don't want to remove the blanket "Deny" rule, because that puts the archive at higher risk -- including objects that I don't want to change.
Instead, I wanted to create a fine-grained rule that said *"let us delete these three objects, but nothing else"*.

A "Deny" always beats an "Allow" in an IAM policy document, so I can't modify our developer roles to give us these permissions -- instead, I wrote this function to create a *temporary* role with these permissions, which we could then assume to run the deletion.
There's less risk of accidentally deleting something that we weren't planning to delete, because there shouldn't be an IAM policy that allows its deletion.

It's not until I finished that I realised this could be more general-purpose, and used to experiment with IAM policy documents.



## Usage: how can somebody else use this?

Read the code in [iam_tester.py](iam_tester.py), then the working example in [example.py](example.py).

There are probably some hidden assumptions about how we use IAM roles at Wellcome, but you might get it working.



## License

MIT.
